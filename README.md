# AI Relay Hub（豆包版）

这是一个基于 Flask 的轻量服务，用来完成下面几类能力：

1. 外部接口调用豆包，生成结构化行情分析
2. 公众号纯文本群发测试
3. 固定问题 -> 豆包生成 -> 公众号群发
4. 固定问题 -> 豆包生成短报 -> 按 openid 单发公众号客服消息

## 配置方式

当前项目配置分为两层：

- [config.py](/E:/workspace/demo/ReportFlow/config.py)
- `config_local.py`

规则如下：

- `config.py`：仓库版本，只放占位符配置，可以提交到 GitHub
- `config_local.py`：本地真实配置，不提交到 GitHub

当前版本不依赖 `.env`。

## 本地配置方式

首次使用时，在项目根目录新建：

`config_local.py`

参考内容：

```python
from config import Config as BaseConfig


class Config(BaseConfig):
    DOUBAO_API_KEY = "你的豆包 Key"
    DOUBAO_MODEL = "你的 Endpoint ID"
    WX_GZH_APPID = "你的公众号 AppID"
    WX_GZH_APPSECRET = "你的公众号 AppSecret"

    WX_GZH_TEST_APPID = "openid单发测试用公众号 AppID"
    WX_GZH_TEST_APPSECRET = "openid单发测试用公众号 AppSecret"
    WX_GZH_TEST_OPENID = "openid单发测试用户"
```

这个文件已经加入 `.gitignore`，不会被提交。

## 安装依赖

```bash
pip install -r requirements.txt
```

或：

```bash
pip install flask requests
```

## 启动服务

```bash
python app.py
```

服务地址：

```text
http://localhost:5000
```

## 接口说明

### 1. 豆包测试接口

`POST /ask`

作用：

- 只调用豆包
- 返回分析结果
- 不做公众号发送

请求示例：

```json
{
  "question": "今天金乡大蒜行情怎么样？"
}
```

### 2. 公众号手动群发测试接口

`POST /gzh/broadcast`

作用：

- 手动输入一段文本
- 走微信公众号群发
- 支持 `dry_run`

Dry run 示例：

```json
{
  "content": "这是一条公众号群发测试消息",
  "dry_run": true
}
```

正式发送示例：

```json
{
  "content": "这是一条公众号群发测试消息",
  "dry_run": false
}
```

### 3. 固定问题 -> 豆包生成 -> 公众号群发

`POST /gzh/report/push`

作用：

- 使用固定问题
- 调豆包生成适合公众号阅读的纯文本报告
- 直接走公众号群发
- 支持 `dry_run`

固定问题：

```text
帮我收集今天金乡大蒜的行情(报价)信息，同时预测后续行情走势，汇总成一份800字左右的报告
```

Dry run 示例：

```json
{
  "dry_run": true
}
```

正式发送示例：

```json
{
  "dry_run": false
}
```

### 4. 固定问题 -> 豆包生成短报 -> 按 openid 单发

`POST /gzh/openid/send`

作用：

- 不需要传任何参数
- 内部固定问题调用豆包
- 自动生成更短的公众号客服消息文本
- 发送给 `config_local.py` 中配置的测试 `openid`

请求示例：

```json
{}
```

成功返回示例：

```json
{
  "success": true,
  "openid": "o3Yuq6NeY8aUKSZCEthIrtnfePMA",
  "content": "金乡大蒜行情简报（2026年4月14日）...",
  "wechat_result": {
    "errcode": 0,
    "errmsg": "ok"
  }
}
```

## 长报和短报的区别

### `/gzh/report/push`

- 用于公众号群发
- 内容相对更长
- 适合 700-900 字左右简报

### `/gzh/openid/send`

- 用于按 openid 单发客服消息
- 微信文本消息长度更敏感
- 已单独压缩为短报格式
- 更适合 300-500 字左右的手机通知消息

## 为什么不用 Markdown

当前公众号发送走的是纯文本消息接口，不是富文本文章接口。

所以这版建议使用：

- 纯文本标题
- 纯文本分段
- 不使用 `#`、`*`、`-` 这类 Markdown 符号

这样在公众号文本消息里最稳，排版也更容易控制。

## 返回示例

成功：

```json
{
  "success": true,
  "result": "报告内容",
  "duration_ms": 12345,
  "timestamp": "2026-04-14 10:00:00"
}
```

失败：

```json
{
  "success": false,
  "error": "error message"
}
```

## 使用建议

推荐测试顺序：

1. 先测 `/ask`，确认豆包输出正常
2. 再测 `/gzh/broadcast` 的 `dry_run`
3. 再测 `/gzh/report/push` 的 `dry_run`
4. 再测 `/gzh/openid/send`
5. 最后再正式触发群发或正式单发

## 注意事项

- 微信公众号后台需要先配置 IP 白名单
- `dry_run=true` 不会真正发消息
- 公众号群发和 openid 单发是两条不同通道
- openid 单发仍可能受到公众号客服消息规则限制
