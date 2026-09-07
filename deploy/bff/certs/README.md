# AgentArts 网关中间证书补充

`globalsign-rsa-ov-2018.pem` 是公开的 GlobalSign RSA OV SSL CA 2018 中间证书，不是私钥、网关叶子证书或新增私有根 CA。

- 来源：[GlobalSign 官方 OrganizationSSL Intermediate Certificates](https://support.globalsign.com/ca-certificates/intermediate-certificates/organizationssl-intermediate-certificates)，RSA / Issued before July 27, 2026 一节。
- 发行者：GlobalSign Root CA - R3。
- 有效期：2018-11-21 至 2028-11-21。
- DER SHA-256：`b676ffa3179e8812093a1b5eafee876ae7a6aaf231078dad1bfb21cd2893764a`。
- 2026-09-06 已用 `openssl verify -CAfile <certifi.where()> globalsign-rsa-ov-2018.pem` 验证可链至现有公共根，结果 OK。

该文件仅用于当前 AgentArts 网关缺失中间链的客户端兼容配置。默认不加载、不打入 BFF 镜像，不修改 OS 全局信任库。部署者需要显式只读挂载并设置 `JINDIAO_BFF_TLS_TRUST_STORE=certifi` 与 `JINDIAO_BFF_TLS_CA_FILE`。

应用保留 certifi 原有根、证书和主机名验证，补充指定 PEM，并禁用 partial-chain 信任。缺失/损坏文件会阻止启动，不会降级为不验证。不要加入未经核验的根/叶子证书；运维提供额外根证书仍会扩展信任，因此此路径只能由可信部署者控制。

应优先由网关侧修复完整服务端证书链。若网关更换发行者或该中间证书过期，必须重新验证，不能下载并盲目信任新证书。
