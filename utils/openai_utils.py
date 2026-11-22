# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit
import re
import json
import time
import os
import threading
import base64
import mimetypes

from openai import OpenAI  # type: ignore
from dotenv import load_dotenv  # type: ignore

try:
    import httpx2 as http_client_lib
except ImportError:
    import httpx as http_client_lib

dot_file = os.path.join(os.path.dirname(__file__), "../.env")
load_dotenv(dot_file)

_openai_client = None
_openai_client_lock = threading.Lock()
DEFAULT_CPA_BASE_URL = "http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1"
CPA_TUNNEL_PROXY_PORT = 3130


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def reset_openai_client() -> None:
    global _openai_client
    with _openai_client_lock:
        _openai_client = None


def _resolve_openai_base_url() -> str | None:
    return os.environ.get("OPENAI_BASE_URL") or (
        DEFAULT_CPA_BASE_URL if _is_truthy_env("PAPER_ORCHESTRA_CPA") else None
    )


def _read_claude_settings_token(settings_path: str | None = None) -> str | None:
    try:
        with open(settings_path or os.path.expanduser("~/.claude/settings.json"),
                  encoding="utf-8") as file:
            token = json.load(file).get("env", {}).get("ANTHROPIC_AUTH_TOKEN")
            return token if isinstance(token, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def _resolve_openai_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key and _is_truthy_env("PAPER_ORCHESTRA_CPA"):
        key = _read_claude_settings_token()
    if not key:
        raise ValueError("Set OPENAI_API_KEY or configure a Claude settings token for CPA.")
    return key


def _replace_proxy_port(proxy_url: str, port: int = CPA_TUNNEL_PROXY_PORT) -> str:
    try:
        parts = urlsplit(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
        host = parts.hostname
    except ValueError:
        host = None
    if not host:
        raise ValueError("HTTPS_PROXY must contain a valid proxy host.")
    userinfo, separator, _ = parts.netloc.rpartition("@")
    host = f"[{host}]" if ":" in host else host
    return urlunsplit(parts._replace(netloc=f"{userinfo}{separator}{host}:{port}"))


def _build_tunnel_http_client() -> http_client_lib.Client | None:
    if not _is_truthy_env("PAPER_ORCHESTRA_CPA_TUNNEL"):
        return None
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        raise ValueError("Set HTTPS_PROXY to use the CPA tunnel.")
    return http_client_lib.Client(proxy=_replace_proxy_port(proxy))


def _redact_secret(text: str, secret: str | None) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def get_openai_client() -> OpenAI:
    """Returns a shared OpenAI client, creating it on first use.

    The client is created lazily so that OPENAI_API_KEY is only required when
    an OpenAI model is actually called (e.g. Gemini-only setups don't need it).
    """
    global _openai_client
    if _openai_client is None:
        with _openai_client_lock:
            if _openai_client is None:
                key = _resolve_openai_api_key()
                base_url = _resolve_openai_base_url()
                http_client = _build_tunnel_http_client()
                try:
                    _openai_client = OpenAI(
                        api_key=key,
                        **({"base_url": base_url} if base_url is not None else {}),
                        **({"http_client": http_client} if http_client is not None else {}),
                    )
                except Exception as exc:
                    error = _redact_secret(str(exc), key)
                else:
                    return _openai_client
                raise RuntimeError(f"Could not initialize OpenAI: {error}")
    return _openai_client


def parse_openai_json_results(response: str):
    if not response or not isinstance(response, str):
        return None

    json_pattern = r"```json(.*?)```"
    matches = re.findall(json_pattern, response, re.DOTALL)

    if not matches:
        json_pattern = r"(\{.*\}|\[.*\])"
        matches = re.findall(json_pattern, response, re.DOTALL)

    for json_string in matches:
        json_string = json_string.strip()
        try:
            parsed_json = json.loads(json_string)
            return parsed_json
        except json.JSONDecodeError:
            try:
                json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
                json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
                json_string_clean = re.sub(
                    r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
                )

                parsed_json = json.loads(json_string_clean)
                return parsed_json
            except json.JSONDecodeError:
                continue

    try:
        start_idx = response.find("{")
        end_idx = response.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    try:
        start_idx = response.find("[")
        end_idx = response.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_string = response[start_idx : end_idx + 1]
            json_string_clean = re.sub(r"[\x00-\x1F\x7F]", "", json_string)
            json_string_clean = re.sub(r",\s*([\]}])", r"\1", json_string_clean)
            json_string_clean = re.sub(
                r'\\(?![\\"/bfnrtu])', r"\\\\", json_string_clean
            )
            return json.loads(json_string_clean)
    except Exception:
        pass

    return None  # No valid JSON found


def call_openai_models_with_content(
    content: str | list[dict[str, object]],
    model_name: str,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    result_parsing_func: Callable[[str], object] = parse_openai_json_results,
    generation_configs: dict[str, object] | None = None,
    check_parsed_response_not_none: bool = True,
    system_prompt: str | None = None,
):
    raw_response = None
    parsed_response = None
    openai_client = get_openai_client()

    for attempt in range(max_retries):
        try:
            messages = []
            if system_prompt:
                role = "developer" if model_name.startswith(("o1", "o3")) else "system"
                messages.append({"role": role, "content": system_prompt})

            messages.append({"role": "user", "content": content})

            response = openai_client.chat.completions.create(
                model=model_name,
                messages=messages,
                **(generation_configs or {}),
            )
            raw_response = response.choices[0].message.content
            parsed_response = result_parsing_func(raw_response)

            if check_parsed_response_not_none:
                assert (
                    parsed_response is not None
                ), f"Parsed response is None, Here is the raw response:\n{raw_response}"

            return {
                "raw_response": raw_response,
                "parsed_response": parsed_response,
            }

        except Exception as e:
            error = _redact_secret(str(e), openai_client.api_key)
            print(_redact_secret(
                f"Warning: OpenAI API call (model={model_name}) failed on attempt {attempt + 1} of {max_retries}. Error: {error}",
                openai_client.api_key,
            ))

        if attempt + 1 == max_retries:
            raise RuntimeError(
                f"OpenAI API call failed after {max_retries} attempts: {error}"
            )

        delay = base_interval_sec * (2 ** (attempt - 1)) if attempt > 0 else 0
        if delay > 0:
            time.sleep(delay)

    print("Error: Exited retry loop unexpectedly.")
    return {
        "raw_response": raw_response,
        "parsed_response": parsed_response,
    }


def call_openai_models_with_text_prompt(
    prompt: str,
    model_name: str,
    result_parsing_func: Callable[[str], object] = parse_openai_json_results,
    generation_configs: dict[str, object] | None = None,
    check_parsed_response_not_none: bool = True,
    max_retries: int = 5,
    base_interval_sec: int = 5,
    system_prompt: str | None = None,
):
    return call_openai_models_with_content(
        content=prompt,
        model_name=model_name,
        result_parsing_func=result_parsing_func,
        generation_configs=generation_configs,
        check_parsed_response_not_none=check_parsed_response_not_none,
        max_retries=max_retries,
        base_interval_sec=base_interval_sec,
        system_prompt=system_prompt,
    )


def get_openai_image_part_from_path(image_path: str):
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"
    with open(image_path, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    image_content = [
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}",
            },
        }
    ]
    return image_content
