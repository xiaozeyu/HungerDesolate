//! Bounded evaluation of DST `modinfo.lua` configuration declarations.
//!
//! This module is deliberately not a Tauri command. MOD source is untrusted and
//! must be read from a verified remote artifact before it reaches this boundary.
//! The remote artifact plumbing will call this pure parser in a later step.

use mlua::prelude::{LuaError, LuaTable, LuaValue, LuaVmState};
use mlua::{HookTriggers, Lua, LuaOptions, StdLib};
use serde::Serialize;
use serde_json::{Map, Number, Value};
use std::{cell::Cell, rc::Rc};

const MAX_SOURCE_BYTES: usize = 2 * 1024 * 1024;
const MAX_MEMORY_BYTES: usize = 32 * 1024 * 1024;
const MAX_INSTRUCTIONS: usize = 1_000_000;
const MAX_TABLE_DEPTH: usize = 16;
const MAX_TABLE_ITEMS: usize = 20_000;
const BUDGET_MARKER: &str = "__hunger_desolate_instruction_budget__";

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModSchema {
    pub status: SchemaStatus,
    pub folder_name: String,
    pub locale: String,
    pub metadata: ModMetadata,
    pub fields: Vec<ModField>,
    pub diagnostics: Vec<ModDiagnostic>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) enum SchemaStatus {
    Available,
    Partial,
    NoOptions,
    Unavailable,
    Failed,
}

#[derive(Debug, Clone, Default, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModMetadata {
    pub name: Option<String>,
    pub author: Option<String>,
    pub description: Option<String>,
    pub version: Option<String>,
    pub api_version: Option<i64>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModField {
    pub name: String,
    pub label: String,
    pub hover: Option<String>,
    pub default_value: Option<Value>,
    pub options: Vec<ModOption>,
    pub editable: bool,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModOption {
    pub label: String,
    pub value: Value,
    pub hover: Option<String>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ModDiagnostic {
    pub code: String,
    pub message: String,
}

#[derive(Debug)]
enum ParseFailure {
    InvalidInput(&'static str),
    InstructionBudget,
    MemoryBudget,
    Runtime(String),
    UnsupportedValue(&'static str),
    TableLimit(&'static str),
}

impl ParseFailure {
    fn code(&self) -> &'static str {
        match self {
            Self::InvalidInput(_) => "MOD_INPUT_INVALID",
            Self::InstructionBudget => "MOD_INSTRUCTION_BUDGET_EXCEEDED",
            Self::MemoryBudget => "MOD_MEMORY_BUDGET_EXCEEDED",
            Self::Runtime(_) => "MOD_RUNTIME_ERROR",
            Self::UnsupportedValue(_) => "MOD_VALUE_UNSUPPORTED",
            Self::TableLimit(_) => "MOD_VALUE_LIMIT_EXCEEDED",
        }
    }

    fn message(&self) -> String {
        match self {
            Self::InvalidInput(message)
            | Self::UnsupportedValue(message)
            | Self::TableLimit(message) => (*message).into(),
            Self::InstructionBudget => "MOD 配置执行超出指令预算".into(),
            Self::MemoryBudget => "MOD 配置执行超出内存预算".into(),
            Self::Runtime(message) => format!("MOD 配置执行失败：{message}"),
        }
    }
}

pub(crate) fn parse_modinfo(source: &str, folder_name: &str, locale: &str) -> ModSchema {
    match parse_inner(source, folder_name, locale) {
        Ok(schema) => schema,
        Err(error) => ModSchema {
            status: SchemaStatus::Failed,
            folder_name: folder_name.to_string(),
            locale: locale.to_string(),
            metadata: ModMetadata::default(),
            fields: Vec::new(),
            diagnostics: vec![ModDiagnostic {
                code: error.code().into(),
                message: error.message(),
            }],
        },
    }
}

pub(crate) fn unavailable_schema(folder_name: &str, locale: &str) -> ModSchema {
    ModSchema {
        status: SchemaStatus::Unavailable,
        folder_name: folder_name.to_string(),
        locale: locale.to_string(),
        metadata: ModMetadata::default(),
        fields: Vec::new(),
        diagnostics: vec![ModDiagnostic {
            code: "MOD_ARTIFACT_NOT_READY".into(),
            message: "实例中尚未找到该 MOD 的固定产物".into(),
        }],
    }
}

fn parse_inner(source: &str, folder_name: &str, locale: &str) -> Result<ModSchema, ParseFailure> {
    if source.is_empty()
        || source.len() > MAX_SOURCE_BYTES
        || source.as_bytes().first() == Some(&27)
    {
        return Err(ParseFailure::InvalidInput(
            "MOD 源码为空、过大或包含预编译字节码",
        ));
    }
    if folder_name.is_empty()
        || folder_name.len() > 128
        || folder_name
            .chars()
            .any(|value| value.is_control() || value == '/' || value == '\\')
        || locale.is_empty()
        || locale.len() > 32
        || locale.chars().any(|value| value.is_control())
    {
        return Err(ParseFailure::InvalidInput("MOD 文件夹或语言标识无效"));
    }

    let lua = Lua::new_with(
        StdLib::TABLE | StdLib::STRING | StdLib::MATH,
        LuaOptions::default(),
    )
    .map_err(map_lua_error)?;
    lua.set_memory_limit(MAX_MEMORY_BYTES)
        .map_err(map_lua_error)?;
    install_environment(&lua, folder_name, locale).map_err(map_lua_error)?;
    install_instruction_hook(&lua);
    lua.load(source)
        .set_name("modinfo.lua")
        .exec()
        .map_err(map_lua_error)?;

    let globals = lua.globals();
    let metadata = ModMetadata {
        name: optional_string(&globals, "name")?,
        author: optional_string(&globals, "author")?,
        description: optional_string(&globals, "description")?,
        version: optional_string(&globals, "version")?,
        api_version: optional_integer(&globals, "api_version")?,
    };
    let options_value = globals
        .get::<LuaValue>("configuration_options")
        .map_err(map_lua_error)?;
    let Some(options) = options_value.as_table() else {
        return Ok(ModSchema {
            status: SchemaStatus::NoOptions,
            folder_name: folder_name.to_string(),
            locale: locale.to_string(),
            metadata,
            fields: Vec::new(),
            diagnostics: Vec::new(),
        });
    };
    let fields = read_fields(options)?;
    let status = if fields.is_empty() {
        SchemaStatus::NoOptions
    } else if fields.iter().any(|field| !field.editable) {
        SchemaStatus::Partial
    } else {
        SchemaStatus::Available
    };
    Ok(ModSchema {
        status,
        folder_name: folder_name.to_string(),
        locale: locale.to_string(),
        metadata,
        fields,
        diagnostics: Vec::new(),
    })
}

fn install_environment(lua: &Lua, folder_name: &str, locale: &str) -> Result<(), LuaError> {
    let globals = lua.globals();
    globals.set("folder_name", folder_name)?;
    globals.set("locale", locale)?;
    let selected_locale = locale.to_string();
    let choose_translation = lua.create_function(move |_, table: LuaTable| {
        let selected = table.raw_get::<LuaValue>(selected_locale.as_str())?;
        if !matches!(selected, LuaValue::Nil) {
            return Ok(selected);
        }
        table.raw_get::<LuaValue>(1)
    })?;
    globals.set("ChooseTranslationTable", choose_translation)?;
    Ok(())
}

fn install_instruction_hook(lua: &Lua) {
    let remaining = Rc::new(Cell::new(MAX_INSTRUCTIONS));
    let counter = remaining.clone();
    lua.set_hook(
        HookTriggers::new().every_nth_instruction(1),
        move |_lua, _debug| {
            let value = counter.get();
            if value == 0 {
                return Err(LuaError::runtime(BUDGET_MARKER));
            }
            counter.set(value - 1);
            Ok(LuaVmState::Continue)
        },
    );
}

fn map_lua_error(error: LuaError) -> ParseFailure {
    let text = error.to_string();
    if text.contains(BUDGET_MARKER) {
        ParseFailure::InstructionBudget
    } else if text.to_ascii_lowercase().contains("memory") {
        ParseFailure::MemoryBudget
    } else {
        ParseFailure::Runtime(text)
    }
}

fn optional_string(table: &LuaTable, key: &str) -> Result<Option<String>, ParseFailure> {
    match table.get::<LuaValue>(key).map_err(map_lua_error)? {
        LuaValue::Nil => Ok(None),
        LuaValue::String(value) => Ok(Some(
            value
                .to_str()
                .map_err(|_| ParseFailure::UnsupportedValue("MOD 文本不是 UTF-8"))?
                .to_string(),
        )),
        _ => Err(ParseFailure::UnsupportedValue("MOD 元数据类型不受支持")),
    }
}

fn optional_integer(table: &LuaTable, key: &str) -> Result<Option<i64>, ParseFailure> {
    match table.get::<LuaValue>(key).map_err(map_lua_error)? {
        LuaValue::Nil => Ok(None),
        LuaValue::Integer(value) => Ok(Some(value)),
        _ => Err(ParseFailure::UnsupportedValue("MOD API 版本不是整数")),
    }
}

fn read_fields(options: &LuaTable) -> Result<Vec<ModField>, ParseFailure> {
    let mut fields = Vec::new();
    for value in options.sequence_values::<LuaValue>() {
        if fields.len() >= MAX_TABLE_ITEMS {
            return Err(ParseFailure::TableLimit("MOD 配置项数量超出限制"));
        }
        let value = value.map_err(map_lua_error)?;
        let Some(field) = value.as_table() else {
            return Err(ParseFailure::UnsupportedValue("MOD 配置项不是表"));
        };
        fields.push(read_field(field)?);
    }
    Ok(fields)
}

fn read_field(field: &LuaTable) -> Result<ModField, ParseFailure> {
    let name = required_string(field, "name")?;
    let label = optional_string(field, "label")?.unwrap_or_else(|| name.clone());
    let hover = optional_string(field, "hover")?;
    let default_value = match field.get::<LuaValue>("default").map_err(map_lua_error)? {
        LuaValue::Nil => None,
        value => Some(lua_value_to_json(&value, 0)?),
    };
    let mut options = Vec::new();
    let mut editable = true;
    let mut reason = None;
    let option_value = field.get::<LuaValue>("options").map_err(map_lua_error)?;
    if let Some(option_table) = option_value.as_table() {
        for value in option_table.sequence_values::<LuaValue>() {
            let value = value.map_err(map_lua_error)?;
            let Some(option) = value.as_table() else {
                editable = false;
                reason = Some("配置候选项不是表".into());
                break;
            };
            let option_value = option.get::<LuaValue>("data").map_err(map_lua_error)?;
            let option_value = match lua_value_to_json(&option_value, 0) {
                Ok(value) => value,
                Err(error) => {
                    editable = false;
                    reason = Some(error.message());
                    break;
                }
            };
            let option_label =
                optional_string(option, "description")?.unwrap_or_else(|| option_value.to_string());
            options.push(ModOption {
                label: option_label,
                value: option_value,
                hover: optional_string(option, "hover")?,
            });
        }
    }
    if default_value
        .as_ref()
        .is_some_and(|value| value.is_object() || value.is_array())
    {
        editable = false;
        reason = Some("复杂默认值暂不支持表单编辑".into());
    }
    Ok(ModField {
        name,
        label,
        hover,
        default_value,
        options,
        editable,
        reason,
    })
}

fn required_string(table: &LuaTable, key: &str) -> Result<String, ParseFailure> {
    optional_string(table, key)?.ok_or(ParseFailure::UnsupportedValue("MOD 配置项缺少名称"))
}

fn lua_value_to_json(value: &LuaValue, depth: usize) -> Result<Value, ParseFailure> {
    if depth >= MAX_TABLE_DEPTH {
        return Err(ParseFailure::TableLimit("MOD 配置表嵌套层级超出限制"));
    }
    match value {
        LuaValue::Nil => Ok(Value::Null),
        LuaValue::Boolean(value) => Ok(Value::Bool(*value)),
        LuaValue::Integer(value) => Ok(Value::Number(Number::from(*value))),
        LuaValue::Number(value) if value.is_finite() => Number::from_f64(*value)
            .map(Value::Number)
            .ok_or(ParseFailure::UnsupportedValue("MOD 配置包含非有限数字")),
        LuaValue::String(value) => Ok(Value::String(
            value
                .to_str()
                .map_err(|_| ParseFailure::UnsupportedValue("MOD 配置字符串不是 UTF-8"))?
                .to_string(),
        )),
        LuaValue::Table(table) => table_to_json(table, depth + 1),
        _ => Err(ParseFailure::UnsupportedValue(
            "MOD 配置包含函数、线程或用户数据",
        )),
    }
}

fn table_to_json(table: &LuaTable, depth: usize) -> Result<Value, ParseFailure> {
    let mut object = Map::new();
    let mut array = Vec::new();
    let mut array_only = true;
    let mut count = 0;
    for pair in table.pairs::<LuaValue, LuaValue>() {
        count += 1;
        if count > MAX_TABLE_ITEMS {
            return Err(ParseFailure::TableLimit("MOD 配置表项数量超出限制"));
        }
        let (key, value) = pair.map_err(map_lua_error)?;
        let converted = lua_value_to_json(&value, depth)?;
        match key {
            LuaValue::Integer(index) if index > 0 => {
                if index as usize != array.len() + 1 {
                    array_only = false;
                }
                array.push(converted);
            }
            LuaValue::String(key) => {
                array_only = false;
                object.insert(
                    key.to_str()
                        .map_err(|_| ParseFailure::UnsupportedValue("MOD 配置键不是 UTF-8"))?
                        .to_string(),
                    converted,
                );
            }
            _ => {
                return Err(ParseFailure::UnsupportedValue(
                    "MOD 配置键不是字符串或正整数",
                ))
            }
        }
    }
    if array_only && object.is_empty() {
        Ok(Value::Array(array))
    } else {
        for (index, value) in array.into_iter().enumerate() {
            object.insert((index + 1).to_string(), value);
        }
        Ok(Value::Object(object))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evaluates_dynamic_configuration_options_with_game_environment() {
        let source = r#"
            local values = {}
            for index = 1, 3 do
                table.insert(values, { description = "等级 " .. index, data = index })
            end
            local labels = { zh = "模式", en = "Mode" }
            name = "Example"
            api_version = 10
            configuration_options = {
                { name = "mode", label = ChooseTranslationTable(labels), options = values, default = 2 },
            }
        "#;
        let schema = parse_modinfo(source, "workshop-123", "zh");
        assert_eq!(schema.status, SchemaStatus::Available);
        assert_eq!(schema.metadata.name.as_deref(), Some("Example"));
        assert_eq!(schema.fields[0].label, "模式");
        assert_eq!(schema.fields[0].options[2].value, serde_json::json!(3));
    }

    #[test]
    fn uses_numeric_translation_fallback() {
        let schema = parse_modinfo(
            "configuration_options = {{ name = 'mode', label = ChooseTranslationTable({ '备用' }), default = false }}",
            "workshop-123",
            "zh",
        );
        assert_eq!(schema.fields[0].label, "备用");
        assert_eq!(
            schema.fields[0].default_value,
            Some(serde_json::json!(false))
        );
    }

    #[test]
    fn rejects_require_and_reports_failure_instead_of_faking_a_schema() {
        let schema = parse_modinfo(
            "local value = require('unsafe')\nconfiguration_options = {}",
            "workshop-123",
            "zh",
        );
        assert_eq!(schema.status, SchemaStatus::Failed);
        assert_eq!(schema.fields.len(), 0);
        assert_eq!(schema.diagnostics[0].code, "MOD_RUNTIME_ERROR");
    }

    #[test]
    fn preserves_zero_and_false_values() {
        let schema = parse_modinfo(
            "configuration_options = {{ name = 'zero', default = 0 }, { name = 'off', default = false }}",
            "workshop-123",
            "zh",
        );
        assert_eq!(schema.status, SchemaStatus::Available);
        assert_eq!(schema.fields[0].default_value, Some(serde_json::json!(0)));
        assert_eq!(
            schema.fields[1].default_value,
            Some(serde_json::json!(false))
        );
    }

    #[test]
    fn rejects_infinite_loops_by_budget() {
        let schema = parse_modinfo("while true do end", "workshop-123", "zh");
        assert_eq!(schema.status, SchemaStatus::Failed);
        assert_eq!(
            schema.diagnostics[0].code,
            "MOD_INSTRUCTION_BUDGET_EXCEEDED"
        );
    }
}
