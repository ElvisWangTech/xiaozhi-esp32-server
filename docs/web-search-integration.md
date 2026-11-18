# 联网搜索插件使用指南

## 功能简介

联网搜索插件 `web_search` 支持在对话过程中实时联网搜索信息并返回结果。插件支持三个搜索源：秘塔（Metaso）、Tavily和Serply，用户可根据需要选择其中一个。

## API Key申请指南

目前我们适配了`秘塔搜索`、`Tavily搜索`和`Serply搜索`。
- Tavily搜索：每个月1000次免费额度。
- 秘塔搜索：拥有较为优质的国内数据源。
- Serply搜索：返回谷歌搜索结果，注册赠送2500次免费额度，额度用完后需要付费。

## API Key申请指南

### 方式一：使用秘塔搜索

- 访问 [秘塔搜索API](https://metaso.cn/search-api/api-keys)，注册并登录账号
- 在API密钥管理页面，点击"创建新的Key"
- 复制生成的API Key（以 `mk-` 为前缀），这是配置所需的关键信息

### 方式二：使用Tavily搜索

- 访问 [Tavily控制台](https://app.tavily.com/home)，注册并登录账号
- 在控制台中创建API Key
- 复制生成的API Key（以 `tvly-` 为前缀），这是配置所需的关键信息

### 方式三：使用Serply搜索

- 访问 [Serply](https://serply.io)，注册并登录账号
- 在控制台中创建API Key
- 复制生成的API Key，这是配置所需的关键信息
- 接口说明见 [Serply文档](https://serply.io/docs)

## 配置方式

### 方式1. 使用智控台部署（推荐）

联网搜索默认开启。搜索源和 API Key 配在系统参数里，每个开启了意图识别的角色都会自动继承，不用再进角色的「编辑功能」单独填写。

1. 登录智控台
2. 进入「参数管理」
3. 打开「插件 · 联网搜索」
4. 确认下面几项后保存：
   - `plugins.web_search.enabled`：总开关，默认 `true`
   - `plugins.web_search.provider`：搜索源，填 `metaso`、`tavily` 或 `serply`
   - `plugins.web_search.api_key`：对应搜索源的 API Key。出厂值 `mk-XXXX` 需要换成可用密钥
   - `plugins.web_search.max_results`：返回条数，默认 5
   - `plugins.web_search.description`：告诉模型什么时候调用这个工具，可按需要修改

角色需要使用函数调用或意图大模型。意图识别设为「不使用意图识别」的角色不会加载插件。

设备下次建立对话即生效，不用逐个保存角色。新建角色也会默认勾选联网搜索。某个角色如果在「编辑功能」里填了自己的 API Key，则该角色继续用自己的配置。没填的角色使用系统参数。把 `plugins.web_search.enabled` 改成 `false` 可以关掉这套默认配置。

### 方式2. 单模块xiaozhi-server部署

在 `data/.config.yaml` 中配置：

- 将搜索源填入 `provider`，可选值为 `metaso`、`tavily` 或 `serply`
- 将申请到的API Key填入 `api_key`

```yaml
plugins:
  web_search:
    provider: "metaso"
    api_key: "你的API Key"
```

如需自定义返回结果数量和工具描述，可额外配置 `max_results` 和 `description`：

```yaml
plugins:
  web_search:
    provider: "metaso"
    description: "联网搜索工具。当用户明确需要联网搜索问题时使用此工具。"
    max_results: 5
    api_key: "你的API Key"
```

同时在 `functions` 列表中确保已启用 `web_search`：

```yaml
plugins:
  functions:
    - web_search
```

配置完成后重启服务即可生效。
