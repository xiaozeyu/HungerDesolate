#!/bin/bash
# 创建测试 release 数据（仅用于验证创建流程，不能运行真实游戏）

set -e

HOST_ID="${1:-test-host-id}"
MANAGEMENT_ROOT="${2:-/tmp/famine-hub-test}"

echo "警告：此脚本仅创建测试数据，不能运行真实 DST 服务器"
echo "HOST_ID: $HOST_ID"
echo "MANAGEMENT_ROOT: $MANAGEMENT_ROOT"
echo ""

# 创建目录结构
mkdir -p "$MANAGEMENT_ROOT/releases/staging/test-release-001"

# 生成假的产物文件
echo "fake DST server binary" > "$MANAGEMENT_ROOT/releases/staging/test-release-001/dst_server.tar"
ARTIFACT_DIGEST=$(sha256sum "$MANAGEMENT_ROOT/releases/staging/test-release-001/dst_server.tar" | awk '{print $1}')

# 创建 release.json
cat > "$MANAGEMENT_ROOT/releases/staging/test-release-001/release.json" <<EOF
{
  "schemaVersion": 1,
  "releaseId": "test-release-001",
  "platform": "linux/amd64",
  "image": "dst-server-test:latest",
  "imageDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "dstBuild": "000000",
  "artifactDigest": "sha256:$ARTIFACT_DIGEST",
  "scriptVersion": "1.0.0-test",
  "verified": true,
  "commands": {
    "master": ["echo", "fake master"],
    "caves": ["echo", "fake caves"]
  }
}
EOF

# 创建 runtime.json（模拟验证通过）
cat > "$MANAGEMENT_ROOT/releases/staging/test-release-001/runtime.json" <<EOF
{
  "schemaVersion": 1,
  "releaseId": "test-release-001",
  "runtimeVerified": true,
  "verification": "passed",
  "verifiedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "networkId": "test-network-id",
  "containerIds": {
    "master": "test-container-master",
    "caves": "test-container-caves"
  }
}
EOF

# 创建 catalog.json
cat > "$MANAGEMENT_ROOT/releases/catalog.json" <<EOF
{
  "schemaVersion": 1,
  "hostId": "$HOST_ID",
  "catalogDigest": "sha256:test-catalog-digest",
  "releases": [{
    "releaseId": "test-release-001",
    "platform": "linux/amd64",
    "image": "dst-server-test:latest",
    "imageDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "dstBuild": "000000",
    "artifactDigest": "sha256:$ARTIFACT_DIGEST",
    "scriptVersion": "1.0.0-test",
    "verified": true,
    "runtimeVerified": true,
    "commands": {
      "master": ["echo", "fake master"],
      "caves": ["echo", "fake caves"]
    }
  }]
}
EOF

echo ""
echo "✅ 测试 release 数据已创建"
echo "路径: $MANAGEMENT_ROOT/releases/"
echo ""
echo "下一步："
echo "1. 确保主机管理目录已初始化"
echo "2. 将 catalog.json 放到远端 /var/lib/famine-hub/releases/catalog.json"
echo "3. 在创建对话框中点击'重新检查'应该能看到 1 个可用 release"
echo "4. 此时可以尝试创建容器（会失败在 Docker 阶段，因为没有真实镜像）"
echo ""
echo "⚠️  警告：这不是真实的 DST 服务器，无法启动游戏"
