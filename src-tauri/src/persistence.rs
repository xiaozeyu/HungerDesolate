use crate::{
    dto::{
        AuthKind, CommandError, CommandResult, ConnectionState, DeleteServerRequest,
        DeleteServerResult, SaveServerRequest, ServerSummary,
    },
    server_input::{self, MAX_REVISION},
};
use rusqlite::{Connection, OptionalExtension, Result, TransactionBehavior};
use serde::{de::DeserializeOwned, Serialize};
use std::path::Path;
use uuid::Uuid;

const SCHEMA_VERSION: i64 = 4;
const SERVER_COLUMNS: &str =
    "server_id,display_name,host,ssh_port,username,auth_kind,host_id,record_version";

pub(crate) fn open(path: &Path) -> Result<Connection> {
    let connection = Connection::open(path)?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(connection)
}
pub fn initialize(path: &Path) -> Result<()> {
    migrate(&mut open(path)?)
}
fn migrate(connection: &mut Connection) -> Result<()> {
    let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let version: i64 = tx.pragma_query_value(None, "user_version", |row| row.get(0))?;
    if version > SCHEMA_VERSION {
        return Err(rusqlite::Error::InvalidQuery);
    }
    if version < 1 {
        tx.execute_batch(include_str!("../migrations/0001_connections.sql"))?;
    }
    if version < 2 {
        tx.execute_batch(include_str!("../migrations/0002_local_operations.sql"))?;
    }
    if version < 3 {
        tx.execute_batch(include_str!("../migrations/0003_ssh_connections.sql"))?;
    }
    if version < 4 {
        tx.execute_batch(include_str!("../migrations/0004_host_initialization.sql"))?;
    }
    tx.execute(
        "DELETE FROM ssh_challenges WHERE expires_at < unixepoch()",
        [],
    )?;
    tx.execute(
        "DELETE FROM credential_receipts WHERE created_at < unixepoch() - 90 * 86400",
        [],
    )?;
    tx.execute("DELETE FROM host_initialization_plans WHERE (started=0 OR finished=1) AND created_at < unixepoch() - 90 * 86400", [])?;
    tx.pragma_update(None, "user_version", SCHEMA_VERSION)?;
    tx.execute(
        "DELETE FROM local_operations WHERE created_at < unixepoch() - 90 * 86400",
        [],
    )?;
    tx.commit()
}
fn server_row(row: &rusqlite::Row<'_>) -> Result<ServerSummary> {
    let kind: String = row.get(5)?;
    let auth_kind = match kind.as_str() {
        "key" => AuthKind::Key,
        "password" => AuthKind::Password,
        _ => return Err(rusqlite::Error::InvalidQuery),
    };
    Ok(ServerSummary {
        server_id: row.get(0)?,
        display_name: row.get(1)?,
        host: row.get(2)?,
        ssh_port: row.get(3)?,
        username: row.get(4)?,
        auth_kind,
        host_id: row.get(6)?,
        record_version: row.get(7)?,
        connection_state: ConnectionState::Disconnected,
    })
}
pub fn list_servers(path: &Path) -> Result<Vec<ServerSummary>> {
    let connection = open(path)?;
    let mut statement = connection.prepare(&format!(
        "SELECT {SERVER_COLUMNS} FROM servers ORDER BY display_name,server_id"
    ))?;
    let rows = statement.query_map([], server_row)?.collect();
    rows
}
pub(crate) fn get_server(
    connection: &Connection,
    id: &str,
    operation: &'static str,
) -> CommandResult<ServerSummary> {
    connection
        .query_row(
            &format!("SELECT {SERVER_COLUMNS} FROM servers WHERE server_id=?1"),
            [id],
            server_row,
        )
        .optional()
        .map_err(|_| CommandError::storage(operation))?
        .ok_or_else(|| {
            CommandError::new(
                "SERVER_NOT_FOUND",
                "validation",
                operation,
                "连接记录已不存在",
                "返回主机列表并刷新",
            )
        })
}
fn replay<T: DeserializeOwned>(
    connection: &Connection,
    id: &str,
    payload: &str,
    operation: &'static str,
) -> CommandResult<Option<T>> {
    let used_for_credentials: bool = connection
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM credential_receipts WHERE operation_id=?1)",
            [id],
            |row| row.get(0),
        )
        .map_err(|_| CommandError::storage(operation))?;
    if used_for_credentials {
        return Err(CommandError::new(
            "OPERATION_ID_REUSED",
            "conflict",
            operation,
            "操作标识已用于凭据变更",
            "刷新核实原操作后再发起新的修改",
        ));
    }
    let saved: Option<(String, String)> = connection
        .query_row(
            "SELECT payload,result FROM local_operations WHERE operation_id=?1",
            [id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|_| CommandError::storage(operation))?;
    match saved {
        None => Ok(None),
        Some((previous, result)) if previous == payload => serde_json::from_str(&result)
            .map(Some)
            .map_err(|_| CommandError::storage(operation)),
        Some(_) => Err(CommandError::new(
            "OPERATION_ID_REUSED",
            "conflict",
            operation,
            "同一操作标识已用于不同输入",
            "先刷新核实原操作结果，再重新编辑",
        )),
    }
}
fn record<T: Serialize>(
    connection: &Connection,
    id: &str,
    payload: &str,
    result: &T,
    operation: &'static str,
) -> CommandResult<()> {
    let value = serde_json::to_string(result).map_err(|_| CommandError::storage(operation))?;
    connection
        .execute(
            "INSERT INTO local_operations(operation_id,payload,result) VALUES (?1,?2,?3)",
            rusqlite::params![id, payload, value],
        )
        .map_err(|_| CommandError::storage(operation))?;
    Ok(())
}
pub fn save_server(path: &Path, request: SaveServerRequest) -> CommandResult<ServerSummary> {
    let _guard = crate::ssh_state::mutation_guard(path)?;
    save_in_connection(
        &mut open(path).map_err(|_| CommandError::storage("saveServer"))?,
        request,
    )
}
fn save_in_connection(
    connection: &mut Connection,
    mut request: SaveServerRequest,
) -> CommandResult<ServerSummary> {
    let operation = "saveServer";
    server_input::validate_envelope(
        request.schema_version,
        &request.request_id,
        &request.operation_id,
        operation,
    )?;
    request.input = server_input::normalize(request.input)?;
    match (&request.server_id, request.expected_record_version) {
        (None, None) => {}
        (Some(id), Some(version)) if (1..MAX_REVISION).contains(&version) => {
            server_input::validate_id(id, operation, "serverId")?
        }
        _ => {
            return Err(CommandError::invalid(
                operation,
                "expectedRecordVersion",
                "编辑必须提供连接标识与有效记录版本",
            ))
        }
    }
    let payload=serde_json::json!({"kind":"save","serverId":request.server_id,"expectedRecordVersion":request.expected_record_version,"input":request.input}).to_string();
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    if let Some(saved) = replay(&tx, &request.operation_id, &payload, operation)? {
        return Ok(saved);
    }
    let id = match request.server_id {
        Some(id) => {
            let previous = get_server(&tx, &id, operation)?;
            if Some(previous.record_version) != request.expected_record_version {
                return Err(CommandError::conflict(operation, previous.record_version));
            }
            let input = &request.input;
            let identity_changed = previous.host != input.host
                || previous.ssh_port != input.ssh_port
                || previous.username != input.username
                || previous.auth_kind != input.auth_kind;
            if identity_changed {
                crate::ssh_state::retire_credential(&tx, &id, operation)?;
                tx.execute("DELETE FROM ssh_trust WHERE server_id=?1", [&id])
                    .map_err(|_| CommandError::storage(operation))?;
                tx.execute("DELETE FROM ssh_challenges WHERE server_id=?1", [&id])
                    .map_err(|_| CommandError::storage(operation))?;
            }
            tx.execute("UPDATE servers SET display_name=?1,host=?2,ssh_port=?3,username=?4,auth_kind=?5,record_version=record_version+1,host_id=CASE WHEN ?6 THEN NULL ELSE host_id END,credential_ref=CASE WHEN ?6 THEN NULL ELSE credential_ref END WHERE server_id=?7 AND record_version=?8",
                rusqlite::params![input.display_name,input.host,input.ssh_port,input.username,input.auth_kind.as_str(),identity_changed,id,previous.record_version]).map_err(|_|CommandError::storage(operation))?;
            id
        }
        None => {
            let id = Uuid::new_v4().to_string();
            let input = &request.input;
            tx.execute("INSERT INTO servers(server_id,display_name,host,ssh_port,username,auth_kind) VALUES (?1,?2,?3,?4,?5,?6)",
                rusqlite::params![id,input.display_name,input.host,input.ssh_port,input.username,input.auth_kind.as_str()]).map_err(|_|CommandError::storage(operation))?;
            id
        }
    };
    let result = get_server(&tx, &id, operation)?;
    record(&tx, &request.operation_id, &payload, &result, operation)?;
    tx.commit().map_err(|_| CommandError::storage(operation))?;
    Ok(result)
}
pub fn delete_server(
    path: &Path,
    request: DeleteServerRequest,
) -> CommandResult<DeleteServerResult> {
    let _guard = crate::ssh_state::mutation_guard(path)?;
    delete_in_connection(
        &mut open(path).map_err(|_| CommandError::storage("deleteServer"))?,
        request,
    )
}
fn delete_in_connection(
    connection: &mut Connection,
    request: DeleteServerRequest,
) -> CommandResult<DeleteServerResult> {
    let operation = "deleteServer";
    server_input::validate_envelope(
        request.schema_version,
        &request.request_id,
        &request.operation_id,
        operation,
    )?;
    server_input::validate_id(&request.server_id, operation, "serverId")?;
    if request.expected_record_version == 0 || request.expected_record_version > MAX_REVISION {
        return Err(CommandError::invalid(
            operation,
            "expectedRecordVersion",
            "记录版本无效",
        ));
    }
    let payload=serde_json::json!({"kind":"delete","serverId":request.server_id,"expectedRecordVersion":request.expected_record_version}).to_string();
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|_| CommandError::storage(operation))?;
    if let Some(saved) = replay(&tx, &request.operation_id, &payload, operation)? {
        return Ok(saved);
    }
    let previous = get_server(&tx, &request.server_id, operation)?;
    if previous.record_version != request.expected_record_version {
        return Err(CommandError::conflict(operation, previous.record_version));
    }
    crate::ssh_state::retire_credential(&tx, &request.server_id, operation)?;
    tx.execute(
        "DELETE FROM servers WHERE server_id=?1 AND record_version=?2",
        rusqlite::params![request.server_id, request.expected_record_version],
    )
    .map_err(|_| CommandError::storage(operation))?;
    let result = DeleteServerResult {
        server_id: request.server_id,
    };
    record(&tx, &request.operation_id, &payload, &result, operation)?;
    tx.commit().map_err(|_| CommandError::storage(operation))?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_database_migration_is_repeatable() -> Result<()> {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        migrate(&mut connection)?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        assert_eq!(version, SCHEMA_VERSION);
        let count: i64 =
            connection.query_row("SELECT count(*) FROM servers", [], |row| row.get(0))?;
        assert_eq!(count, 0);
        Ok(())
    }

    #[test]
    fn rejects_newer_database_without_modifying_it() -> Result<()> {
        let mut connection = Connection::open_in_memory()?;
        connection.pragma_update(None, "user_version", SCHEMA_VERSION + 1)?;
        assert!(migrate(&mut connection).is_err());
        let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
        assert_eq!(version, SCHEMA_VERSION + 1);
        Ok(())
    }

    #[test]
    fn connection_schema_excludes_secrets_and_validates_ports() -> Result<()> {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        let columns: Vec<String> = connection
            .prepare("PRAGMA table_info(servers)")?
            .query_map([], |row| row.get(1))?
            .collect::<Result<_>>()?;
        assert!(!columns
            .iter()
            .any(|column| ["password", "token", "private_key"].contains(&column.as_str())));
        let insert = "INSERT INTO servers(server_id,display_name,host,ssh_port,username,auth_kind) VALUES (?1,?2,?3,?4,?5,?6)";
        assert!(connection
            .execute(
                insert,
                rusqlite::params!["a", "主机", "host", 0, "user", "key"]
            )
            .is_err());
        connection.execute(
            insert,
            rusqlite::params!["a", "主机", "host", 22, "user", "key"],
        )?;
        assert!(connection
            .execute(
                insert,
                rusqlite::params!["a", "主机", "host", 22, "user", "key"]
            )
            .is_err());
        Ok(())
    }

    fn create_request() -> SaveServerRequest {
        SaveServerRequest {
            schema_version: 1,
            request_id: Uuid::new_v4().to_string(),
            operation_id: Uuid::new_v4().to_string(),
            server_id: None,
            expected_record_version: None,
            input: crate::dto::ServerInput {
                display_name: "上海主机".into(),
                host: "EXAMPLE.COM".into(),
                ssh_port: 22,
                username: "dst-admin".into(),
                auth_kind: AuthKind::Key,
            },
        }
    }
    fn edit_request(server: &ServerSummary) -> SaveServerRequest {
        let mut request = create_request();
        request.server_id = Some(server.server_id.clone());
        request.expected_record_version = Some(server.record_version);
        request
    }
    fn delete_request(server: &ServerSummary) -> DeleteServerRequest {
        DeleteServerRequest {
            schema_version: 1,
            request_id: Uuid::new_v4().to_string(),
            operation_id: Uuid::new_v4().to_string(),
            server_id: server.server_id.clone(),
            expected_record_version: server.record_version,
        }
    }
    type TestResult = std::result::Result<(), Box<dyn std::error::Error>>;

    #[test]
    fn previous_schema_upgrade_preserves_connections() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        connection.execute_batch(include_str!("../migrations/0001_connections.sql"))?;
        connection.pragma_update(None, "user_version", 1)?;
        connection.execute("INSERT INTO servers(server_id,display_name,host,username,auth_kind) VALUES ('existing','old','host','user','key')",[])?;
        migrate(&mut connection)?;
        assert_eq!(
            get_server(&connection, "existing", "test")?.display_name,
            "old"
        );
        assert_eq!(
            connection.query_row("SELECT count(*) FROM local_operations", [], |row| row
                .get::<_, i64>(0))?,
            0
        );
        Ok(())
    }

    #[test]
    fn ssh_schema_upgrade_preserves_trust_and_initialization_pending_is_unique() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        connection.execute_batch(include_str!("../migrations/0001_connections.sql"))?;
        connection.execute_batch(include_str!("../migrations/0002_local_operations.sql"))?;
        connection.execute_batch(include_str!("../migrations/0003_ssh_connections.sql"))?;
        connection.pragma_update(None, "user_version", 3)?;
        connection.execute("INSERT INTO servers(server_id,display_name,host,username,auth_kind) VALUES ('existing','old','host','user','key')",[])?;
        connection.execute("INSERT INTO ssh_trust(server_id,algorithm,host_key,fingerprint) VALUES ('existing','ssh-ed25519',X'0102','SHA256:fixture')",[])?;
        migrate(&mut connection)?;
        assert_eq!(
            connection.query_row(
                "SELECT fingerprint FROM ssh_trust WHERE server_id='existing'",
                [],
                |row| row.get::<_, String>(0)
            )?,
            "SHA256:fixture"
        );
        connection.execute("INSERT INTO host_initialization_plans(plan_id,operation_id,server_id,record_version,payload,started) VALUES ('a','oa','existing',1,'{}',1)",[])?;
        connection.execute("INSERT INTO host_initialization_plans(plan_id,operation_id,server_id,record_version,payload) VALUES ('b','ob','existing',1,'{}')",[])?;
        assert!(connection
            .execute(
                "UPDATE host_initialization_plans SET started=1 WHERE plan_id='b'",
                []
            )
            .is_err());
        connection.execute(
            "UPDATE host_initialization_plans SET finished=1 WHERE plan_id='a'",
            [],
        )?;
        assert_eq!(
            connection.execute(
                "UPDATE host_initialization_plans SET started=1 WHERE plan_id='b'",
                []
            )?,
            1
        );
        Ok(())
    }

    #[test]
    fn saves_edits_and_deletes_with_cas_and_idempotency() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        let request = create_request();
        let server = save_in_connection(&mut connection, request.clone())?;
        assert_eq!(server.host, "example.com");
        assert_eq!(server.record_version, 1);
        let same = save_in_connection(&mut connection, request.clone())?;
        assert_eq!(same.server_id, server.server_id);
        let mut wrong = request;
        wrong.input.display_name = "other".into();
        assert!(
            matches!(save_in_connection(&mut connection,wrong),Err(error) if error.code=="OPERATION_ID_REUSED")
        );
        let mut edit = edit_request(&server);
        edit.input.display_name = "新名称".into();
        let updated = save_in_connection(&mut connection, edit.clone())?;
        assert_eq!(updated.record_version, 2);
        assert_eq!(updated.server_id, server.server_id);
        edit.operation_id = Uuid::new_v4().to_string();
        assert!(
            matches!(save_in_connection(&mut connection,edit),Err(error) if error.code=="REVISION_CONFLICT")
        );
        assert!(
            matches!(delete_in_connection(&mut connection,delete_request(&server)),Err(error) if error.code=="REVISION_CONFLICT")
        );
        let delete = delete_request(&updated);
        delete_in_connection(&mut connection, delete.clone())?;
        delete_in_connection(&mut connection, delete)?;
        assert!(
            matches!(get_server(&connection,&server.server_id,"test"),Err(error) if error.code=="SERVER_NOT_FOUND")
        );
        Ok(())
    }

    #[test]
    fn endpoint_edits_invalidate_host_binding_but_rename_preserves_it() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        let server = save_in_connection(&mut connection, create_request())?;
        connection.execute(
            "UPDATE servers SET host_id='known-host' WHERE server_id=?1",
            [&server.server_id],
        )?;
        let renamed = save_in_connection(&mut connection, edit_request(&server))?;
        assert_eq!(renamed.host_id.as_deref(), Some("known-host"));
        let mut edit = edit_request(&renamed);
        edit.input.host = "192.0.2.42".into();
        let moved = save_in_connection(&mut connection, edit)?;
        assert_eq!(moved.host_id, None);
        assert!(matches!(
            moved.connection_state,
            ConnectionState::Disconnected
        ));
        Ok(())
    }

    #[test]
    fn operation_record_failure_rolls_back_metadata_change() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        connection.execute_batch("CREATE TRIGGER reject_operation BEFORE INSERT ON local_operations BEGIN SELECT RAISE(ABORT, 'test failure'); END;")?;
        assert!(save_in_connection(&mut connection, create_request()).is_err());
        assert_eq!(
            connection.query_row("SELECT count(*) FROM servers", [], |row| row
                .get::<_, i64>(0))?,
            0
        );
        Ok(())
    }

    #[test]
    fn local_operations_cannot_reuse_save_id_for_delete() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        let save = create_request();
        let server = save_in_connection(&mut connection, save.clone())?;
        let mut delete = delete_request(&server);
        delete.operation_id = save.operation_id;
        assert!(
            matches!(delete_in_connection(&mut connection,delete),Err(error) if error.code=="OPERATION_ID_REUSED")
        );
        assert!(get_server(&connection, &server.server_id, "test").is_ok());
        Ok(())
    }

    #[test]
    fn invalid_input_and_schema_leave_database_empty() -> TestResult {
        let mut connection = Connection::open_in_memory()?;
        migrate(&mut connection)?;
        let mut request = create_request();
        request.input.host = "ssh://bad".into();
        assert!(
            matches!(save_in_connection(&mut connection,request),Err(error) if error.code=="INPUT_INVALID")
        );
        let mut request = create_request();
        request.schema_version = 9;
        assert!(
            matches!(save_in_connection(&mut connection,request),Err(error) if error.code=="SCHEMA_UNSUPPORTED")
        );
        assert_eq!(
            connection.query_row("SELECT count(*) FROM servers", [], |row| row
                .get::<_, i64>(0))?,
            0
        );
        Ok(())
    }

    #[test]
    fn file_database_reopens_and_concurrent_clients_cannot_overwrite() -> TestResult {
        let path = std::env::temp_dir().join(format!("hunger-desolate-{}.sqlite3", Uuid::new_v4()));
        let result = (|| -> TestResult {
            initialize(&path)?;
            let saved = save_server(&path, create_request())?;
            assert_eq!(list_servers(&path)?[0].server_id, saved.server_id);
            let request_a = edit_request(&saved);
            let request_b = edit_request(&saved);
            let path_a = path.clone();
            let path_b = path.clone();
            let a = std::thread::spawn(move || save_server(&path_a, request_a));
            let b = std::thread::spawn(move || save_server(&path_b, request_b));
            let a = a.join().map_err(|_| "client a thread failed")?;
            let b = b.join().map_err(|_| "client b thread failed")?;
            assert!(a.is_ok() ^ b.is_ok());
            let error = match (a, b) {
                (Err(error), _) | (_, Err(error)) => error,
                _ => return Err("expected one revision conflict".into()),
            };
            assert!(["REVISION_CONFLICT", "CONNECTION_BUSY"].contains(&error.code));
            let latest = list_servers(&path)?.remove(0);
            assert_eq!(latest.record_version, 2);
            delete_server(&path, delete_request(&latest))?;
            assert!(list_servers(&path)?.is_empty());
            Ok(())
        })();
        let cleanup = std::fs::remove_file(&path);
        result?;
        cleanup?;
        Ok(())
    }
}
