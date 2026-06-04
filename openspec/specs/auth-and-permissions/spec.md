# auth-and-permissions Specification

## Purpose
提供用户名+密码的注册/登录体系，以 PBKDF2 哈希存储密码、Cookie Token 自动登录，并以三级角色（admin/super/user）控制功能与数据可见范围，未登录不可使用系统。

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

系统 SHALL 以 HttpOnly Cookie 承载 Token，有效期 7 天，凭 Token 可自动识别当前用户。登出 SHALL 失效该 Token 并清除 Cookie。

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
