# ECS 本地打包与服务器部署

状态：两阶段脚本已实现；完整实机部署仍需维护窗口验收。更新：2026-09-08。

流程明确分为 **本地打包 → 手动传输/登录 → ECS 上部署**，不再把账号认证、上传和服务切换放进一个本地命令。原 `bin/ecs` 与 SSH/TOML 配置样例已移除；本地与 AgentArts 入口的行为保持不变。

## 1. 本地打包

本机只需要 Python 3.11+，不需要 Docker、SSH 登录或服务器密码。入口优先使用项目 `.venv/bin/python`，也可设置 `JINDIAO_DEPLOY_PYTHON`。

```bash
./bin/ecs-package --dry-run
./bin/ecs-package
```

默认输出 `artifacts/ecs-packages/<发布号>.tar.gz` 和同名 `.tar.gz.sha256`。发布号自动生成（UTC 时间与随机后缀）；也可以显式指定一个未使用过的名称：

```bash
./bin/ecs-package --release ecs-20260908-manual-01
```

可用 `--output-dir /absolute/path/packages` 改变输出位置；已有同名压缩包或校验文件会拒绝覆盖。`make ecs-package ARGS='--release ecs-20260908-manual-01'` 等价。无需 `--apply`；`--dry-run` 不产生文件。

压缩包内只有一个发布目录：

```text
ecs-20260908-manual-01/
├── source.tar.gz          冻结的运行时代码及逐文件清单
├── ecs_host.py            Python 3.6+ 服务端控制器
├── deploy.sh              在 ECS 上执行的入口
├── start.sh               启动已部署的主容器
├── restart.sh             重启主容器，等待健康
├── stop.sh                停止主容器，保留数据
├── _service.sh            ECS shell 生命周期实现
├── _service-common.sh     公共 Docker 启停函数
├── config.example.json    非敏感服务器配置样例
└── package.json           发布号、包内文件 SHA-256
```

源码包含当前工作区未提交/未跟踪的业务修改，按运行时白名单排除 dotenv、日志、历史 artifacts、私钥等。打包不构建镜像、不发起网络请求、不使用账号或密码；打包后继续修改源码，需要另生成新包。它是**源码部署包，不是预构建镜像**，ECS 构建时仍需要网络和足够磁盘。

## 2. 手动上传和校验

以下使用显式示例发布号；若采用自动发布号，将命令中的名称替换为脚本输出。确认远端没有同名包/目录再传输，避免覆盖旧版本。

```bash
scp artifacts/ecs-packages/ecs-20260908-manual-01.tar.gz artifacts/ecs-packages/ecs-20260908-manual-01.tar.gz.sha256 root@1.95.121.114:/opt/jindiao/releases/
ssh root@1.95.121.114
```

使用正常 SSH/SCP 的密码提示或已有密钥。密码只在提示处输入，**不要作为命令参数追加，不写进部署脚本、配置或仓库**。保留主机指纹验证，首次连接先核对指纹。

登录 ECS 后：

```bash
cd /opt/jindiao/releases
sha256sum -c ecs-20260908-manual-01.tar.gz.sha256
umask 077
test ! -e ecs-20260908-manual-01 && tar --keep-old-files --no-same-owner -xzf ecs-20260908-manual-01.tar.gz
cd ecs-20260908-manual-01
cp -n config.example.json config.json
```

校验失败、目录已存在或解压失败时停止，不要继续运行。校验文件用于发现传输/内容变化，不替代可信来源和主机身份验证。只执行自己生成并核对过的包。

## 3. 配置与服务器前提

编辑包目录的 `config.json`，确认以下字段：

| 字段 | 作用 |
| --- | --- |
| `remote_root` | 既有部署根目录，默认 `/opt/jindiao` |
| `container` | 既有主容器，默认 `jindiao-ecs` |
| `docker_bin` / `docker_host` | 既有 Docker CLI 和 Unix socket，不误用系统其他 daemon |
| `runtime_env` | 服务器现有业务配置文件路径，**不是密码内容** |
| `port` / `preflight_port` | 正式/隔离预检端口，必须不同，均仅绑定回环 |

样例 `runtime_env` 指向晚间部署记录中的 `/opt/jindiao/runtime/ecs-20260907-230420.env`，执行前确认仍为目标配置，尤其不要把预算退回早期联调值。服务器脚本使用 JSON 配置，无 TOML 解析依赖，也不包含 SSH host、password 或远程 Python 字段。

服务器前提：

- Bash、Python **3.6+** 标准库（含 Linux `fcntl`）、已运行的 Docker；无需安装新 Python 或业务 Python 包到宿主机。应用镜像继续固定 Python 3.11。可用 `JINDIAO_ECS_PYTHON` 指定服务器解释器。
- 包目录必须是 `<remote_root>/releases/<发布号>`；已有部署目录、healthy 主容器和原数据卷。此脚本不新建 ECS、不安装 Docker、不修改防火墙、不配置公网入口。
- 当前只支持 host network、单 worker、`attached + local`，`/app/artifacts` 为一个可写 named volume。候选须为 AMD64、非 root，UID/GID 与旧容器一致。不兼容的挂载/执行模式需人工迁移。
- `runtime_env` 是部署账户所有的普通文件，非符号链接，权限 0600；密钥仅在服务器发布目录内形成另一份 0600 配置快照，不进入下载包或回执。
- Docker 能拉取基础镜像并访问 GitCode/Python 依赖源；磁盘要容纳新镜像、新完整数据卷及保留的旧版本。
- **在整个发布窗口停止客户端新请求，并等待已有 Run 结束。** 应用没有原子化停止受理/排空接口，空闲检查不能替代流量隔离；`--maintenance-confirmed` 是操作者对这一前提的确认。

已只读确认现有服务器密码登录可用、Docker 24.0.9/x86_64、宿主机 Python 3.6.8、容器内 Python 3.11.16，原服务健康。控制器还在真实 Python 3.6.8 中以内存方式通过导入、CLI 帮助与只读 Docker 查询；这不代表新包的构建/迁移/切换已验收。

## 4. 在 ECS 上预览和部署

从解压后的包目录执行：

```bash
./deploy.sh --config config.json --dry-run
```

默认也是预览。它检查配置和包内校验值，不调用 Docker、不读取业务 dotenv、不写文件，不验证运行中服务或网络可用性。不要把 `DRY RUN` 当作实机健康预检完成。

确认维护窗口后：

```bash
./deploy.sh --config config.json --apply --maintenance-confirmed
```

执行顺序：

1. 校验包内脚本、配置样例和源码压缩包摘要；通过文件锁防止同一部署根目录内两个发布并行。已准备/执行过的发布目录拒绝直接重跑。
2. 校验业务配置权限，解开源码快照并逐文件校验，复制服务器运行配置。读取旧容器真实数据卷、镜像与 restart policy，不写死原卷名。
3. 构建 AMD64 候选镜像，以独立预检卷和端口启动，检查健康、运行配置、UID/GID 以及 `pip check`。不调用真实模型/天眼查。
4. 停止预检容器，检查原卷没有非终态 Run，再停止主服务。**从此有停机时间**。复制原卷到全新卷，保留报告、事件和幂等索引，校验所有历史文件并保留所有权与权限。
5. 将旧容器重命名保留且禁用自动重启；新主容器使用新卷、独立运行配置、单 worker、回环监听启动。
6. 等待新服务健康，复核历史文件，设置 `unless-stopped`，写入回执；确认结果后才恢复客户端流量。

预检不创建业务 Run，因此不合并预检卷。源卷始终只读复制；禁止两个运行中的业务容器共写同一业务卷。

## 5. 启停现有服务

首次部署/发布新代码仍使用 `deploy.sh`。包内的三个 shell 脚本只管理 `config.json` 指定的现有主容器，不重建镜像、不创建新卷，不操作备份容器：

```bash
./start.sh --config config.json
./restart.sh --config config.json --maintenance-confirmed
./stop.sh --config config.json --maintenance-confirmed
```

均支持 `--dry-run`、`--help` 和 `--wait-timeout 180`。默认配置是脚本旁的 `config.json`，配置好后可省略 `--config`。`start.sh` 在服务已运行时不重启；没有已部署容器时明确失败。`stop.sh` 对已停止/不存在容器可重复执行；启动、重启均等待 Docker health check。

ECS 启停需要 util-linux `flock`，与发布控制器共用 `<remote_root>/deployment.lock`，避免和发布并发。Python 3.6 仅校验/读取现有 JSON 配置，shell 直接调用指定 Docker daemon；不读取业务 dotenv、不接受 SSH 密码。重启/停止前检查持久化 Run 元数据；有非终态任务、状态无法读取时拒绝操作。先停止客户端新请求，维护确认不能省略；该检查不是原子化流量排空。

`restart.sh` 保留容器原镜像、环境变量和挂载，编辑运行 `.env` 不会被 restart 自动应用。需要新镜像/配置时重新打包并执行发布流程。仅含旧脚本的 v1 包不会被悄悄修改；新包的清单版本为 v2，新增 shell 文件均包含在校验清单中。

## 6. 回执、失败与回滚

服务器 `<发布目录>/receipt.json` 记录新旧镜像、原/新卷、备份容器及核对摘要，不包含密钥或报告全文；发布结果还会打印到终端。回执不会自动传回本机，需要时由操作者另行下载。

- 构建/预检失败：旧服务保持运行。
- 切换失败：尽力恢复旧容器原名、原卷、原 restart policy，并检查健康，状态为 `rolled-back`。预检清理失败不会阻断旧服务恢复。
- 回滚失败：状态为 `rollback-incomplete-manual-recovery-required`，保持维护状态并人工介入。
- SSH 断线、进程/主机异常：先核查容器和回执，不能仅凭客户端退出码判断结果；不要盲目重跑或删除发布目录。
- 成功后人工回滚：停止新请求、等待任务结束并备份新卷。已有新数据时，不能直接切回旧卷，否则新数据会暂时不可见；需另行处理兼容迁移。

旧容器、原卷、失败新卷和预检资源保留供核查，不自动 `prune`。回滚窗口结束后，只按回执中的精确名称选择性清理，不整体删除部署根目录或所有 Docker 卷。

## 7. 验收边界

自动检查不包含收费的真实业务 smoke。需要时另行确认预算，按 [API 文档](../api/README.md) 发起无 `scenario_id` 的真实 single/multi 请求，验证报告 GET、SSE 终态和重放。`partial` 可表示来源覆盖不足，不能标成完整源覆盖。现有固定 Mock 场景探针不直接用于正式环境。

维护结束后可以通过仅本机绑定的隧道访问：

```bash
ssh -N -L 127.0.0.1:18080:127.0.0.1:8080 root@1.95.121.114
```

然后访问 `http://127.0.0.1:18080/ping`。原业务证据见 [晚间部署记录](../ecs-redeployment-2026-09-07-evening.md)。拆分脚本的离线测试和 Python 3.6 兼容检查不替代维护窗口内完整实机发布验收。
