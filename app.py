from datetime import datetime
from time import perf_counter
import json
import re

import requests
from flask import Flask, jsonify, request

try:
    from config_local import Config
except ImportError:
    from config import Config


app = Flask(__name__)


class AppError(Exception):
    """应用级异常，统一返回 JSON 错误结构。"""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_message_content(content):
    """发送前做一层文本归一化，避免 \\uXXXX 被原样发到微信。"""
    if not content:
        return content

    # 只有命中明显的 unicode 转义格式时才尝试解码，避免误伤正常中文。
    if re.search(r"\\u[0-9a-fA-F]{4}", content):
        try:
            return content.encode("utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            return content

    return content


def trim_utf8_bytes(content, max_bytes=1800):
    """按 UTF-8 字节数裁剪文本，避免微信文本消息超长。"""
    if not content:
        return content

    encoded = content.encode("utf-8")
    if len(encoded) <= max_bytes:
        return content

    trimmed = encoded[:max_bytes]
    while trimmed:
        try:
            return trimmed.decode("utf-8").rstrip() + "\n\n【提示】内容较长，已自动精简发送。"
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]

    return content[:200]


def format_wechat_report(text):
    """把豆包输出整理成更适合公众号文本消息阅读的格式。"""
    if not text:
        return text

    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]

    if not lines:
        return text

    # 强制使用服务端当天日期作为标题，避免模型自行编造日期。
    now = datetime.now()
    title = f"金乡大蒜行情简报（{now.year}年{now.month}月{now.day}日）"

    normalized = [title, ""]
    section_map = {
        "今日报价": "【今日报价】",
        "行情变化原因": "【行情变化原因】",
        "后续走势判断": "【后续走势判断】",
        "风险提示": "【风险提示】",
        "简要结论": "【简要结论】",
    }

    current_section = None
    for raw_line in lines[1:]:
        line = raw_line.replace("：", "").replace(":", "").strip()

        matched_section = None
        for key, section_title in section_map.items():
            if key in line:
                matched_section = section_title
                break

        if matched_section:
            if normalized and normalized[-1] != "":
                normalized.append("")
            normalized.append(matched_section)
            current_section = matched_section
            continue

        # 把今日报价部分保持成一行一个报价，方便手机阅读。
        if current_section == "【今日报价】":
            quote_line = raw_line.strip()
            quote_line = quote_line.replace("（", "").replace("）", "")
            if "全文共" not in quote_line:
                normalized.append(quote_line)
            continue

        # 其他部分保留原始句子，但避免出现连续空行。
        normal_line = raw_line.strip()
        if "全文共" not in normal_line:
            normalized.append(normal_line)

    # 清理首尾和多余空行
    cleaned = []
    prev_blank = False
    for line in normalized:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank

    return "\n".join(cleaned).strip()


def format_wechat_openid_report(text):
    """把 openid 单发内容整理成更短、更紧凑的手机消息格式。"""
    formatted = format_wechat_report(text)
    if not formatted:
        return formatted

    replacements = {
        "【行情变化原因】": "【行情原因】",
        "【后续走势判断】": "【后续走势】",
        "【风险提示】": "【结论】",
        "【简要结论】": "【结论】",
    }

    for old, new in replacements.items():
        formatted = formatted.replace(old, new)

    # 最终再做一次长度裁剪，确保客服文本消息可发。
    return trim_utf8_bytes(formatted, max_bytes=1800)


def validate_doubao_config():
    """校验豆包必要配置。"""
    if not Config.DOUBAO_API_KEY:
        raise AppError("DOUBAO_API_KEY is not configured", 500)
    if not Config.DOUBAO_MODEL:
        raise AppError("DOUBAO_MODEL is not configured", 500)


def validate_gzh_config():
    """校验公众号必要配置。"""
    if not Config.WX_GZH_APPID:
        raise AppError("WX_GZH_APPID is not configured", 500)
    if not Config.WX_GZH_APPSECRET:
        raise AppError("WX_GZH_APPSECRET is not configured", 500)


def validate_gzh_test_config():
    """校验 openid 单发测试配置。"""
    if not Config.WX_GZH_TEST_APPID:
        raise AppError("WX_GZH_TEST_APPID is not configured", 500)
    if not Config.WX_GZH_TEST_APPSECRET:
        raise AppError("WX_GZH_TEST_APPSECRET is not configured", 500)
    if not Config.WX_GZH_TEST_OPENID:
        raise AppError("WX_GZH_TEST_OPENID is not configured", 500)


def call_doubao(question):
    """使用默认 Prompt 调用豆包。"""
    return call_doubao_with_prompt(Config.SYSTEM_PROMPT, question)


def call_doubao_with_prompt(system_prompt, question):
    """按指定 Prompt 调用豆包，并返回文本结果与耗时。"""
    started_at = perf_counter()
    headers = {
        "Authorization": f"Bearer {Config.DOUBAO_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": Config.DOUBAO_MODEL,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": system_prompt,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": question,
                    }
                ],
            },
        ],
        "temperature": Config.DOUBAO_TEMPERATURE,
        "max_output_tokens": Config.DOUBAO_MAX_OUTPUT_TOKENS,
        "thinking": {"type": "disabled"},
    }

    try:
        response = requests.post(
            Config.DOUBAO_API_URL,
            headers=headers,
            json=payload,
            timeout=Config.DOUBAO_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise AppError("Doubao API request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"Doubao API request failed: {exc}", 502) from exc

    if not response.ok:
        response_text = response.text.strip()
        detail = f": {response_text}" if response_text else ""
        raise AppError(
            f"Doubao API failed with status {response.status_code}{detail}",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse Doubao API response", 502) from exc

    # 优先读取官方直出的 output_text。
    if data.get("output_text"):
        return data["output_text"], round((perf_counter() - started_at) * 1000)

    # 兼容 output -> content 这种嵌套结构。
    output = data.get("output", [])
    for item in output:
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"], round((perf_counter() - started_at) * 1000)
        # 某些接入点可能只返回 summary，做一层兜底兼容。
        for summary in item.get("summary", []):
            if summary.get("type") in {"summary_text", "output_text", "text"} and summary.get("text"):
                return summary["text"], round((perf_counter() - started_at) * 1000)

    raise AppError("No text content found in Doubao response", 502)


def push_to_qywx(ai_result):
    """企业微信 webhook 推送，当前仅作兼容保留。"""
    if not Config.WECHAT_WEBHOOK_URL:
        return False

    ai_result = normalize_message_content(ai_result)

    payload = {
        "msgtype": "text",
        "text": {
            "content": ai_result,
        },
    }

    try:
        response = requests.post(
            Config.WECHAT_WEBHOOK_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat webhook request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat webhook request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat webhook failed with status {response.status_code}",
            502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat webhook response", 502) from exc

    if result.get("errcode") != 0:
        raise AppError(f"WeChat push failed: {result.get('errmsg', 'unknown error')}", 502)

    return True


def get_gzh_access_token():
    """获取微信公众号 access_token。"""
    return get_gzh_access_token_by_credentials(Config.WX_GZH_APPID, Config.WX_GZH_APPSECRET)


def get_gzh_access_token_by_credentials(appid, appsecret):
    """按指定 AppID/AppSecret 获取公众号 access_token。"""
    if not appid or not appsecret:
        raise AppError("appid or appsecret is not configured", 500)

    try:
        response = requests.get(
            f"{Config.WX_GZH_API_BASE}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": appid,
                "secret": appsecret,
            },
            timeout=30,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat Official Account token request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat Official Account token request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat Official Account token request failed with status {response.status_code}",
            502,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat Official Account token response", 502) from exc

    access_token = data.get("access_token")
    if not access_token:
        raise AppError(
            f"WeChat Official Account token fetch failed: {data.get('errmsg', data)}",
            502,
        )

    return access_token


def broadcast_gzh_text(content):
    """调用公众号 sendall 接口，对全体粉丝群发纯文本消息。"""
    content = normalize_message_content(content)
    access_token = get_gzh_access_token()
    payload = {
        "filter": {
            "is_to_all": True,
        },
        "text": {
            "content": content,
        },
        "msgtype": "text",
    }

    try:
        response = requests.post(
            f"{Config.WX_GZH_API_BASE}/cgi-bin/message/mass/sendall",
            params={"access_token": access_token},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=60,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat Official Account broadcast request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat Official Account broadcast request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat Official Account broadcast failed with status {response.status_code}",
            502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat Official Account broadcast response", 502) from exc

    if result.get("errcode") != 0:
        raise AppError(
            f"WeChat Official Account broadcast failed: {result.get('errmsg', result)}",
            502,
        )

    return result


def send_gzh_openid_text(openid, content, appid, appsecret):
    """按 openid 单发公众号客服文本消息。"""
    content = normalize_message_content(content)
    access_token = get_gzh_access_token_by_credentials(appid, appsecret)
    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {
            "content": content,
        },
    }

    try:
        response = requests.post(
            f"{Config.WX_GZH_API_BASE}/cgi-bin/message/custom/send",
            params={"access_token": access_token},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        )
    except requests.Timeout as exc:
        raise AppError("WeChat Official Account openid send request timed out", 504) from exc
    except requests.RequestException as exc:
        raise AppError(f"WeChat Official Account openid send request failed: {exc}", 502) from exc

    if not response.ok:
        raise AppError(
            f"WeChat Official Account openid send failed with status {response.status_code}",
            502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("Failed to parse WeChat Official Account openid send response", 502) from exc

    if result.get("errcode") != 0:
        raise AppError(
            f"WeChat Official Account openid send failed: {result.get('errmsg', result)}",
            502,
        )

    return result


@app.errorhandler(AppError)
def handle_app_error(error):
    """统一业务错误返回。"""
    return jsonify({"success": False, "error": error.message}), error.status_code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """统一兜底异常返回。"""
    return jsonify({"success": False, "error": f"Unexpected error: {error}"}), 500


@app.route("/ask", methods=["POST"])
def ask():
    """接口1：仅调用豆包，返回分析结果。"""
    validate_doubao_config()

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()

    if not question:
        raise AppError("question cannot be empty", 400)

    ai_result, duration_ms = call_doubao(question)
    ai_result = format_wechat_report(ai_result) if "金乡大蒜" in question else ai_result
    pushed = push_to_qywx(ai_result)

    return jsonify(
        {
            "success": True,
            "question": question,
            "result": ai_result,
            "pushed": pushed,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/gzh/broadcast", methods=["POST"])
def gzh_broadcast():
    """接口2：公众号文本群发测试接口。"""
    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    dry_run = bool(data.get("dry_run", True))

    if not content:
        raise AppError("content cannot be empty", 400)

    if dry_run:
        access_token = get_gzh_access_token()
        return jsonify(
            {
                "success": True,
                "dry_run": True,
                "content": content,
                "access_token_ok": bool(access_token),
                "request_payload": {
                    "filter": {"is_to_all": True},
                    "text": {"content": content},
                    "msgtype": "text",
                },
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    result = broadcast_gzh_text(content)
    return jsonify(
        {
            "success": True,
            "content": content,
            "dry_run": False,
            "wechat_result": result,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/gzh/report/push", methods=["POST"])
def gzh_report_push():
    """接口3：固定问题 -> 豆包生成报告 -> 公众号纯文本群发。"""
    validate_doubao_config()
    validate_gzh_config()

    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", True))

    question = Config.FIXED_GARLIC_REPORT_QUESTION
    ai_result, duration_ms = call_doubao_with_prompt(Config.GZH_REPORT_PROMPT, question)
    ai_result = format_wechat_report(ai_result)

    if dry_run:
        access_token = get_gzh_access_token()
        return jsonify(
            {
                "success": True,
                "dry_run": True,
                "question": question,
                "result": ai_result,
                "duration_ms": duration_ms,
                "access_token_ok": bool(access_token),
                "request_payload": {
                    "filter": {"is_to_all": True},
                    "text": {"content": ai_result},
                    "msgtype": "text",
                },
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    result = broadcast_gzh_text(ai_result)
    return jsonify(
        {
            "success": True,
            "dry_run": False,
            "question": question,
            "result": ai_result,
            "duration_ms": duration_ms,
            "wechat_result": result,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


@app.route("/gzh/openid/send", methods=["POST"])
def gzh_openid_send():
    """接口4：固定问题 -> 豆包生成报告 -> 按 openid 单发公众号客服文本消息。"""
    validate_gzh_test_config()
    validate_doubao_config()

    openid = Config.WX_GZH_TEST_OPENID
    if not openid:
        raise AppError("openid cannot be empty", 400)

    question = Config.FIXED_GARLIC_REPORT_QUESTION
    content, duration_ms = call_doubao_with_prompt(Config.GZH_OPENID_REPORT_PROMPT, question)
    content = format_wechat_openid_report(content)

    result = send_gzh_openid_text(
        openid,
        content,
        Config.WX_GZH_TEST_APPID,
        Config.WX_GZH_TEST_APPSECRET,
    )
    return jsonify(
        {
            "success": True,
            "question": question,
            "openid": openid,
            "content": content,
            "duration_ms": duration_ms,
            "wechat_result": result,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
