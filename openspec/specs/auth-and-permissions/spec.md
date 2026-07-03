# auth-and-permissions Specification

## Purpose
提供用户名+密码的注册/登录体系，以 PBKDF2 哈希存储密码、Cookie Token 自动登录，并以三级角色（admin/super/user）控制功能与数据可见范围。未登录用户以「访客」身份（等同普通用户只读权限）浏览只读内容，写操作与管理操作仍需真登录并服务端鉴权。

## Requirements

### Requirement: 用户注册

系统 SHALL 支持用户名 + 密码注册。用户名长度 SHALL 为 2-20 字符且唯一；密码 SHALL 至少 8 位并同时包含大写字母、小写字母、数字与特殊字符。注册成功后系统 SHALL 默认赋予 `user` 角色并下发登录 Cookie。

#### Scenario: 密码不满足复杂度

- **WHEN** 用户以不含特殊字符（或缺大小写/数字、或不足 8 位）的密码注册
- **THEN** 系统拒绝注册并返回具体的密码规则错误

#### Scenario: 用户名重复

- **WHEN** 用户以一个已存在的用户名注册
- **THEN** 系统返回 409，提示该用户名已被注册

#### Scenario: 注册成功

- **WHEN** 用户名合法且密码满足复杂度
- **THEN** 系统创建用户（默认角色 `user`）、以 PBKDF2-SHA256 + 随机盐存储密码哈希，并通过 HttpOnly Cookie 下发 Token

### Requirement: 登录与密码校验

系统 SHALL 校验用户名与密码，密码以 PBKDF2 哈希比对。校验失败 SHALL 返回统一的「用户名或密码错误」，不区分用户名不存在与密码错误。

#### Scenario: 凭据错误

- **WHEN** 用户名不存在或密码不匹配
- **THEN** 系统返回 401，统一提示「用户名或密码错误」

#### Scenario: 登录成功

- **WHEN** 用户名与密码校验通过
- **THEN** 系统签发新 Token、更新 `last_seen`，并通过 Cookie 下发，返回用户的 id/username/role

### Requirement: Cookie Token 自动登录

系统 SHALL 以 HttpOnly Cookie 承载 Token，登录后有效期 1 年（长期保持、不重复弹窗），凭 Token 可自动识别当前用户。登出 SHALL 失效该 Token 并清除 Cookie。

#### Scenario: 凭 Cookie 识别当前用户

- **WHEN** 请求携带有效的 Token Cookie
- **THEN** 系统返回对应用户信息并刷新 `last_seen`

#### Scenario: 登出失效 Token

- **WHEN** 用户登出
- **THEN** 系统将该 Token 置空并删除 Cookie，此后该 Token 不再可用

### Requirement: 三级角色权限

系统 SHALL 提供 admin（超级管理员）、super（超级用户）、user（普通用户）三级角色。用户管理类操作（列出用户、修改角色、删除用户）SHALL 仅 admin 可执行；非 admin 调用 SHALL 返回 403；未携带有效 Token 调用 SHALL 返回 401。

#### Scenario: 非管理员访问用户管理

- **WHEN** 一个 super 或 user 角色（或匿名）调用用户列表/改角色/删用户接口
- **THEN** 系统返回 403（未登录则 401），拒绝操作

#### Scenario: 管理员修改用户角色

- **WHEN** admin 将某用户角色更新为合法值（admin/super/user 之一）
- **THEN** 系统更新该用户角色；若目标角色非法则返回 400

### Requirement: 管理员不可删除自身

系统 SHALL 禁止 admin 删除自己的账号。

#### Scenario: 管理员尝试删除自己

- **WHEN** admin 调用删除用户接口且目标为自身 id
- **THEN** 系统返回 400，提示不能删除自己

### Requirement: 文档导入与删除仅管理员

文档的导入（文件上传、腾讯/企微/Confluence 链接导入）与删除，以及导入历史/失败记录的删除，SHALL 仅 `admin` 角色可执行；服务端 SHALL 强制校验（不依赖前端隐藏）。非管理员调用这些写接口 SHALL 返回 403。文档阅读、查看新旧版本、导入历史查看 SHALL 对所有登录用户开放。

#### Scenario: 超级用户尝试导入/删除

- **WHEN** 一个 `super` 用户调用 `/upload`、`/upload/url` 或 `DELETE /documents/{id}`
- **THEN** 服务端返回 403

### Requirement: 用户行为日志

系统 SHALL 记录用户在平台的主要功能使用行为，至少包括：问答（记录问题）、生成清单（活动+清单名）、导出清单（清单+条目数+文档数）。每条日志含用户、行为类型、详情、时间。记录 SHALL 为"尽力而为"——写入失败绝不影响主功能。系统 SHALL 提供按用户查询行为日志的接口（时间倒序明细 + 各类型次数统计），仅 `admin` 可访问；用户管理界面为每个用户提供查看入口。

#### Scenario: 问答被记录

- **WHEN** 用户发起一次问答
- **THEN** 系统记录一条「问答」行为日志，且即使日志写入异常问答仍正常返回

#### Scenario: 管理员查看用户行为

- **WHEN** 管理员在用户管理界面查看某用户的行为日志
- **THEN** 展示该用户各功能使用次数统计与时间倒序的行为明细；非 admin 调用该接口返回 403

### Requirement: 访客只读访问

系统 SHALL 允许未登录用户以「访客」身份访问只读内容，不强制弹窗登录。`require_login` 依赖对未登录请求 SHALL 返回访客身份对象（`role=user`、`is_guest=True`）而非抛 401。访客权限 SHALL 等同普通用户只读：可用智能问答、查看直播日历、作战地图、保障清单列表/详情/进度；SHALL NOT 执行任何写操作或管理操作。前端 SHALL 默认不弹登录页、允许"以访客身份浏览"，并提供登录入口；导航可见项按有效角色（访客视为 `user`）控制。

#### Scenario: 访客浏览只读内容
- **WHEN** 未登录用户访问问答、直播日历、作战地图查看、清单列表/详情等只读接口
- **THEN** 系统以访客身份放行并返回数据，不强制登录

#### Scenario: 访客尝试写操作被拒
- **WHEN** 访客调用清单生成/勾选/导出、作战地图反馈点赞等写接口
- **THEN** 系统据 `is_guest` 返回 401，要求先登录

### Requirement: 服务端统一鉴权（不得依赖前端作为安全边界）

所有业务接口 SHALL 在服务端校验身份。只读接口对访客放行；写操作与管理操作 SHALL 校验真实登录身份——访客/未登录写操作 401、越权 403。前端菜单隐藏 SHALL NOT 作为安全边界。系统提供统一依赖：`require_login`（未登录返回访客只读身份）、写操作专用依赖（如清单 `_require_login`、作战地图挡 `is_guest`）对访客抛 401、`require_admin` 仅 admin；Settings 等敏感写接口仅 admin。

#### Scenario: 匿名写/管理操作被拒
- **WHEN** 未登录或访客请求文档导入删除、设置、用户管理、清单写操作等接口
- **THEN** 返回 401（越权 403），不允许写操作、不泄露越权数据

### Requirement: 用户数据隔离

涉个人数据的接口 SHALL 以服务端解析的当前用户为准，不以客户端 user_id 或可猜测标识(conversation_id / legacy-{id})决定范围。聊天历史列表、单会话详情、流式追问加载的历史 SHALL 默认仅本人可见可删；admin/super 经显式分支可看全部，普通用户传/猜他人标识被忽略或拒绝。权限测试 SHALL 覆盖垂直(角色)+水平(同角色用户间越权)两维度。

#### Scenario: 越权读删他人聊天历史被阻止
- **WHEN** 普通用户传他人 user_id 查询、或删非本人记录
- **THEN** 查询强制限定本人；删除 403

#### Scenario: 越权读他人单会话/追问历史被阻止
- **WHEN** 用户 B 用用户 A 的 conversation_id 或 legacy-{id} 请求会话详情、或发起流式追问
- **THEN** 会话详情返回空、追问历史限定为 B 本人，不泄露/注入 A 的内容
