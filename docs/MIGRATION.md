# 原项目功能迁移

参考仓库：https://github.com/CatTriplet/nans_habitat_test.git

本次分析版本：29f4462bd599bea3998140023e6df50440e08a63。Axiom 是重新实现的前后端分离平台，没有拷贝原仓库中的业务凭据。

| 原项目 | Axiom |
| --- | --- |
| Django + SimpleUI 页面 | Vue 独立前端 + DRF API |
| UserProfile / Django 权限 | 自定义用户 + Group/Permission + 项目数据权限 |
| NansEnvConfig | 个人环境，保留 cloud/local、服务路由，凭据改为加密 JSON |
| ServiceEndpoint | 项目接口，function_name 对应原 service_name |
| AST 扫描 TestCaseInfo | 模块函数、类方法、优先级和作者的 AST 扫描 |
| Web 同步运行 Pytest | 持久任务队列 + 独立 Worker + 超时/取消 |
| HTML 报告 | 数据库结果、日志、受鉴权的 HTML 下载 |
| Python 业务逻辑、设备控制、SSH/ADB | 通过可信测试目录和上下文适配接入 |
| MQTT stress_engine | 原仓库 manager/scenarios 为空；未扩展为压测平台 |

## 导入环境与接口

在原项目虚拟环境导出。文件包含旧环境密码，不提交到 Git：

    python manage.py dumpdata devices.NansEnvConfig devices.ServiceEndpoint --indent 2 --output legacy.json

在 Axiom 创建项目、目标用户后运行：

    .venv/bin/python manage.py import_legacy /path/to/legacy.json --owner admin --project 1

若原数据有多名环境所有者，必须传 --legacy-owner-id，分别导入到相应用户。命令更新同标识环境及同项目/服务/方法接口，请在新数据库或备份后导入。用户密码、历史报告和执行记录不自动迁入；用户通过新平台创建，用例重新扫描。

## 接入原 Python 用例

1. 原仓库放到独立目录，保留其业务依赖虚拟环境，不作为 Axiom Django 应用加载。
2. 将 integrations/legacy_context.py 复制到原仓库 utils/platform_context.py。
3. 用 rg 搜索旧配置读取器引用，将 import 来源 utils.user_info_data 改为 utils.platform_context；保留 get_full_env_context 函数名。
4. 在 Axiom .env 配置以下绝对路径，再重启 Worker：

    PYTEST_WORKDIR=/path/to/original-repository
    PYTEST_ROOT=/path/to/original-repository/habitat_core/test_cases
    PYTEST_PYTHON=/path/to/original-repository/venv/bin/python

5. 在环境中填写 DB/Redis/MQTT/SSH 私密配置和服务域名，同 owner/code 的 cloud/local 一起快照传给子进程。
6. 选择项目点击“同步 Python 用例”，先运行一条不影响设备状态的用例，再逐步迁移套件。

Worker 注入 AXIOM_CONTEXT_JSON、TEST_USER、TARGET_USERNAME、TARGET_ENV_CODE。适配器输出原 AttributeDict 结构，业务层的设备闭环控制、令牌处理等可继续使用。原代码可能还有自定义依赖或配置，需要按实际测试系统调整；未连接真实 IoT 设备执行验收。
