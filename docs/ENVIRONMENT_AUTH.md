# 环境、系统与认证

环境表示运行目标，例如 test、staging、production；接口定义不属于环境，只绑定 system_key 和相对路径。

示例：

接口 get_user_profile 绑定 user_server 和 /api/v1/users/profile。

测试环境的 user_server 是 https://user.test.example.com，生产环境的 user_server 是 https://user.example.com。

切换环境后，接口定义、代码、用例、断言不变，平台只替换系统地址和该系统的认证 Token。

## 环境配置

一个环境包含：

- 系统配置：用户中心、IoT、场景、订单等 HTTP 系统。每项有 system_key、名称、完整地址、SSL 校验、登录规则和系统级凭据。
- 公共变量与公共请求头：该环境所有 HTTP 系统默认使用。
- 基础设施连接：多个 MySQL、PostgreSQL、Redis、MQTT 连接。每项包含连接名称、主机、端口、用户名、数据库或 DB、密码和附加参数。

密码、Token、系统级凭据和基础设施敏感字段加密保存。读取接口仅返回主机、端口、连接名称和是否已配置凭据。

公共环境所有账号可选择，但仅超级管理员 admin 能维护；私有环境只属于创建账号。

## 被测系统登录

顶部选择环境，再点“登录被测系统”，选择具体系统。每个系统独立配置登录规则，例如：

    {
      "path": "/api/login",
      "body_type": "json",
      "body": {"username": "{{username}}", "password": "{{password}}"},
      "token_path": "$.data.access_token",
      "header_name": "Authorization",
      "prefix": "Bearer",
      "expires_in": 3600
    }

Token 按 Axiom 账号、环境、system_key 加密保存。用户中心和 IoT 系统可以分别登录、分别过期，互不覆盖。登录密码不持久化且不返回前端。

接口启用“使用环境认证 Token”时，只注入该接口 system_key 的 Token。未登录、Token 过期或系统规则变更会阻止执行并提示重新登录。

## 接口与 Python 调用

新建接口填写唯一“代码调用方法名”，例如 get_device_detail；所属系统为 iot_server；路径是 /api/devices/{device_id}。

Python 用例：

    from integrations.api_client import AxiomApiClient

    client = AxiomApiClient()
    response = client.get_device_detail(
        path={"device_id": "light-001"},
        query={"verbose": True}
    ).assert_success()

同一接口在测试、生产环境会自动调用各自 iot_server 地址。动态客户端支持 HTTP 方法、Path/Query/Header/JSON/Form/Text、环境变量、系统级 Token 和超时。
