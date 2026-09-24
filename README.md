# Axiom Backend

Django 5.2 + Django REST Framework 的接口自动化平台后端。独立于 Vue 项目运行，API 前缀为 /api/v1/。

## 本地启动

需要 Python 3.11+。默认 SQLite，无需先安装 Redis 或 MySQL。

    cd /Users/bytedance/Desktop/test_case/axiom-backend
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    cp .env.example .env
    python manage.py migrate
    AXIOM_ADMIN_PASSWORD='自行设置至少8位强密码' python manage.py bootstrap --demo
    python manage.py runserver 127.0.0.1:8000

另一个终端启动任务消费者：

    cd /Users/bytedance/Desktop/test_case/axiom-backend
    .venv/bin/python manage.py runworker

bootstrap 创建三个初始角色和可选演示数据，不覆盖已有用户密码。SQLite 使用单个 Worker；多个 Worker 或正式部署请使用 PostgreSQL。Worker 采用数据库持久队列，HTTP 请求无需等待测试结束，进程异常中断的任务在心跳超时后标记异常，不自动重试有副作用的测试。

## 能力

- JWT 登录、刷新、退出、修改密码；用户新增、编辑、停用。
- 前端 /roles 和 /users 管理角色与账号；Django 仅作为后端权限存储和鉴权引擎，未暴露 Django Admin。
- 项目所有者/成员隔离；公共环境全员可见但仅超级管理员可维护，私有环境绑定账号；后端强制校验。
- 服务接口、HTTP 用例、Python 用例扫描、按顺序执行的测试套件。
- 环境变量和请求模板、响应变量提取、状态码/JSON/文本/响应头/耗时断言。
- 执行快照、异步 Worker、取消、超时、结果明细、HTML 报告和审计日志。
- 私密配置加密存储，不通过读取接口返回；报告按敏感键和已知秘密值脱敏。
- 原仓库 Django fixture 导入与 Python 上下文适配，见 docs/MIGRATION.md。
- 接口保存后可通过动态 Python 客户端调用，见 docs/ENVIRONMENT_AUTH.md。
- 当前环境切换、地址 Key 关联、被测系统登录与 Token 注入、前端接口调试，见 docs/ENVIRONMENT_AUTH.md。

## API

交互文档：http://127.0.0.1:8000/api/v1/docs/

| 路径 | 功能 |
| --- | --- |
| auth/login/、refresh/、me/、logout/、password/ | 身份与个人资料 |
| users/、roles/、roles/permissions/ | 用户、角色与权限 |
| projects/、endpoints/、cases/、suites/ | 测试资产 CRUD |
| environments/、environments/{id}/activate/ | 环境配置与切换 |
| cases/scan/ | 扫描管理员配置的 PYTEST_ROOT |
| runs/ | GET 列表；POST 创建异步执行 |
| runs/{id}/、cancel/、report/ | 明细、取消、下载报告 |
| dashboard/、audit-logs/ | 统计和审计 |

列表统一返回 count/next/previous/results，支持 page、search；项目资源支持 project 筛选。

## 验证

    .venv/bin/python manage.py test automation
    .venv/bin/python manage.py check
    .venv/bin/python manage.py makemigrations --check --dry-run

浏览器端到端测试位于前端 e2e/。后端测试使用隔离数据库，包含实际 Pytest 子进程执行。

## 配置与运行约束

- 在 .env 设置 RUNNER_ALLOWED_HOSTS，逗号分隔的精确主机名；新增测试域名后重启 Worker。HTTP 执行器不跟随重定向，也不使用系统代理。可信主机应由部署网络限制到测试网段。
- secrets 支持 variables、db、redis、mqtt、ssh 对象；修改 secrets 会替换整个私密配置，编辑时留空保留。普通 variables/headers 可读，敏感信息放到 secrets.variables，再通过 {{token}} 引用。
- 环境私密字段和任务快照采用 Fernet 加密，密钥派生自 DJANGO_SECRET_KEY；备份必须包含数据库及密钥。更换密钥前需迁移已有密文。
- Python 用例是受信任代码，不是沙箱。仅扫描/执行管理员配置目录；正式运行请把 Worker 放到独立容器、限制网络和文件权限。
- JSON 路径支持 $.data.id 和 $.items[0].name，不支持完整 JSONPath 过滤表达式。HTTP 支持 JSON/Form/Text，文件上传、多段 multipart 需用 Python 用例。
- 一条参数化 Python node 在报告中算一项，参数实例明细查看 Python 日志。取消 HTTP 用例在当前请求完成或超时后生效。
- 原仓库的 MQTT 压测调度/场景模块尚未实现，本项目不提供完整压测控制台；设备、MQTT、SSH/ADB 业务测试通过旧 Python 代码适配接入。

## 正式部署

使用 PostgreSQL、独立 Worker、反向代理 HTTPS；关闭 DEBUG，设置随机 DJANGO_SECRET_KEY、准确的 ALLOWED_HOSTS/CORS_ALLOWED_ORIGINS 与目标主机名单。生产前使用自己的管理员密码。登录限流默认本地缓存，多实例部署应配置共享缓存与网关限流。

    python manage.py migrate
    python manage.py collectstatic --noinput
    gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
    python manage.py runworker

前端 Nginx 配置代理 /api/ 到 backend:8000。附件式 HTML 报告由后端根据执行结果生成，所有展示内容转义。
