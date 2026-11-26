# 第三方许可声明

荒野中枢（Hunger Desolate）采用 MIT License，版权归 xiaozeyu 所有，详见 [LICENSE](LICENSE)。

本文件汇总随应用分发的第三方组件。各组件的版权归其作者所有；完整许可文本以各组件上游发行包或仓库中的 LICENSE/NOTICE 文件为准。

## 直接运行时依赖

### 前端

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| `@tauri-apps/api` | 2.11.1 | Apache-2.0 OR MIT |
| `vue` | 3.5.38 | MIT |
| `vue-router` | 4.6.4 | MIT |
| `naive-ui` | 2.45.3 | MIT |
| `@lucide/vue` | 1.28.0 | ISC |

### Rust

| crate | 版本 | 许可证 |
| --- | --- | --- |
| `tauri` | 2.11.5 | Apache-2.0 OR MIT |
| `tauri-plugin-dialog` | 2.7.3 | Apache-2.0 OR MIT |
| `rusqlite` | 0.32.1 | MIT |
| `serde` | 1.0.228 | MIT OR Apache-2.0 |
| `serde_json` | 1.0.150 | MIT OR Apache-2.0 |
| `uuid` | 1.23.4 | Apache-2.0 OR MIT |
| `ssh2` | 0.9.6 | MIT OR Apache-2.0 |
| `libssh2-sys` | 0.3.3 | MIT OR Apache-2.0 |
| `openssl-src` | 300.6.1+3.6.3 | MIT/Apache-2.0 |
| `sha2` | 0.10.9 | MIT OR Apache-2.0 |
| `base64` | 0.22.1 | MIT OR Apache-2.0 |
| `zeroize` | 1.9.1 | Apache-2.0 OR MIT |
| `mlua` | 0.10.5 | MIT |
| `reqwest` | 0.13.4 | MIT OR Apache-2.0 |
| `rustls` | 0.23.43 | Apache-2.0 OR ISC OR MIT |
| `ring` | 0.17.14 | Apache-2.0 AND ISC |
| `keyring` | 3.6.3 | MIT OR Apache-2.0 |

## 随二进制分发的本地库

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| SQLite | Public Domain | 由 `rusqlite` 的 `bundled` 特性编译 |
| OpenSSL | Apache-2.0 | 由 `ssh2` 的 `vendored-openssl` 特性编译 |
| libssh2 | BSD-3-Clause | 由 `ssh2` 使用 |
| Lua 5.1 | MIT | 由 `mlua` 的 `vendored` 特性编译 |
| zlib | Zlib | `libssh2-sys` 的压缩依赖 |

> 许可证表达式使用 SPDX 标识；`OR` 表示上游提供多许可选择，`AND` 表示需要同时满足。

## 完整依赖清单

`Cargo.lock` 和 `package-lock.json` 固定了全部直接与传递依赖。发布前建议重新生成并复核完整清单：

```bash
cargo install cargo-license
cargo license --manifest-path src-tauri/Cargo.toml

npx license-checker --production --summary
```

各依赖的 LICENSE/NOTICE 文件保留在上游源码包中；发布二进制时，建议将完整清单和许可文本一并附在安装包或发行版资产中。
