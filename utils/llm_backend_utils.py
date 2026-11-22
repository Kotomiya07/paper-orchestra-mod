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

from collections.abc import Callable, Sequence
import os

from google.genai import types

from utils.agy_utils import call_agy_with_text_prompt
from utils.gemini_utils import (
    call_gemini_with_contents,
    call_gemini_with_images,
    call_gemini_with_text_prompt,
    parse_gemini_json_results,
)
from utils.openai_utils import (
    call_openai_models_with_content,
    call_openai_models_with_text_prompt,
    get_openai_image_part_from_path,
    parse_openai_json_results,
)
from utils.pdf_utils import load_paper
from utils.t3_utils import call_t3_with_contents


VALID_LLM_BACKENDS = {"openai", "gemini", "agy", "t3"}
Parser = Callable[[str], object]
Response = dict[str, object]


def _backend_override(variable: str) -> str:
    backend = os.environ.get(variable, "").strip().lower()
    if backend and backend not in VALID_LLM_BACKENDS:
        raise ValueError(f"{variable} must be one of {', '.join(sorted(VALID_LLM_BACKENDS))}.")
    return backend


def resolve_text_backend(model_name: str) -> str:
    backend = _backend_override("LLM_BACKEND")
    return backend or ("openai" if any(name in model_name.lower() for name in ("gpt", "o1", "o3")) else "gemini")


def resolve_multimodal_backend(model_name: str) -> str:
    return _backend_override("LLM_BACKEND_MULTIMODAL") or resolve_text_backend(model_name)


def is_openai_backend(model_name: str) -> bool:
    return resolve_text_backend(model_name) == "openai"


def resolve_multimodal_model_name(model_name: str) -> str:
    if _backend_override("LLM_BACKEND_MULTIMODAL"):
        return os.environ.get("LLM_MULTIMODAL_MODEL", "").strip() or model_name
    return model_name


def identity_parse(response: str) -> str:
    return response


def get_llm_parser(model_name: str, return_json: bool = True, backend: str | None = None) -> Parser:
    if not return_json:
        return identity_parse
    if (backend or resolve_text_backend(model_name)) == "openai":
        return parse_openai_json_results
    return parse_gemini_json_results


def _combine_system_and_user(prompt: str, system_instruction: str | None) -> str:
    return f"System Instructions:\n{system_instruction}\n\n{prompt}" if system_instruction else prompt


def _wrap_raw_text_response(raw: str, parser: Parser, check_parsed: bool) -> Response:
    parsed = parser(raw)
    if check_parsed and parsed is None:
        raise ValueError("Could not parse the LLM response.")
    return {"raw_response": raw, "parsed_response": parsed}


def _configs(generation_configs: dict[str, object] | None) -> tuple[dict[str, object], str | None]:
    config = dict(generation_configs or {})
    instruction = config.pop("system_instruction", None)
    if instruction is not None and not isinstance(instruction, str):
        raise TypeError("system_instruction must be text.")
    return config, instruction


def _call_text(
    contents: Sequence[str],
    model_name: str,
    generation_configs: dict[str, object] | None,
    system_instruction: str | None,
    tools: Sequence[object] | None,
    check_parsed: bool,
    return_json: bool,
    result_parsing_func: Parser | None,
    *,
    single_prompt: bool = False,
) -> Response:
    backend = resolve_text_backend(model_name)
    parser = result_parsing_func or get_llm_parser(model_name, return_json, backend)
    config, configured_instruction = _configs(generation_configs)
    instruction = system_instruction if system_instruction is not None else configured_instruction
    if tools is not None:
        config["tools"] = list(tools)
    if backend != "gemini" and config.pop("tools", None):
        raise NotImplementedError(f"{backend} does not support tools in this pipeline.")
    args = dict(model_name=model_name, result_parsing_func=parser,
                check_parsed_response_not_none=check_parsed)
    prompt = "\n\n".join(contents)
    if backend == "gemini":
        if instruction is not None:
            config["system_instruction"] = instruction
        if single_prompt:
            return call_gemini_with_text_prompt(prompt=prompt, generation_configs=config, **args)
        return call_gemini_with_contents(
            contents=[types.Part.from_text(text=text) for text in contents],
            generation_configs=config, **args,
        )
    if backend == "openai":
        config.pop("temperature", None)
        if single_prompt:
            return call_openai_models_with_text_prompt(
                prompt=prompt, system_prompt=instruction, generation_configs=config, **args,
            )
        return call_openai_models_with_content(
            content=prompt, system_prompt=instruction, generation_configs=config, **args,
        )
    prompt = _combine_system_and_user(prompt, instruction)
    raw = (
        call_agy_with_text_prompt(prompt, model_name)
        if backend == "agy"
        else call_t3_with_contents(prompt_text=prompt, attachments=[], model_name=model_name)
    )
    return _wrap_raw_text_response(raw, parser, check_parsed)


def call_llm_with_text_prompt(
    prompt: str,
    model_name: str,
    generation_configs: dict[str, object] | None = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Parser | None = None,
) -> Response:
    return _call_text(
        [prompt], model_name, generation_configs, None, None, check_parsed_response_not_none,
        return_json, result_parsing_func, single_prompt=True,
    )


def call_llm_with_contents(
    contents: Sequence[str],
    model_name: str,
    *,
    system_instruction: str | None = None,
    tools: Sequence[object] | None = None,
    generation_configs: dict[str, object] | None = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Parser | None = None,
) -> Response:
    return _call_text(
        contents, model_name, generation_configs, system_instruction, tools,
        check_parsed_response_not_none, return_json, result_parsing_func,
    )


def call_llm_with_images(
    prompt: str,
    images: list[str],
    model_name: str,
    generation_configs: dict[str, object] | None = None,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Parser | None = None,
) -> Response:
    if not images:
        return call_llm_with_text_prompt(
            prompt, model_name, generation_configs, check_parsed_response_not_none,
            return_json, result_parsing_func,
        )
    backend = resolve_multimodal_backend(model_name)
    parser = result_parsing_func or get_llm_parser(model_name, return_json, backend)
    model = resolve_multimodal_model_name(model_name)
    if backend == "agy":
        raise NotImplementedError("agy does not support image attachments.")
    args = dict(model_name=model, result_parsing_func=parser,
                check_parsed_response_not_none=check_parsed_response_not_none)
    if backend == "gemini":
        return call_gemini_with_images(
            prompt=prompt, images=images, generation_configs=generation_configs or {}, **args,
        )
    config, instruction = _configs(generation_configs)
    if config.pop("tools", None):
        raise NotImplementedError(f"{backend} does not support tools in this pipeline.")
    if backend == "t3":
        raw = call_t3_with_contents(
            prompt_text=_combine_system_and_user(prompt, instruction),
            attachments=images, model_name=model,
        )
        return _wrap_raw_text_response(raw, parser, check_parsed_response_not_none)
    config.pop("temperature", None)
    content = [{"type": "text", "text": prompt}]
    for path in images:
        content.extend(get_openai_image_part_from_path(path))
    return call_openai_models_with_content(
        content=content, generation_configs=config, system_prompt=instruction, **args,
    )


def call_llm_with_pdf(
    pdf_path: str,
    prompt: str,
    model_name: str,
    system_instruction: str | None = None,
    temperature: float | None = 0.7,
    check_parsed_response_not_none: bool = True,
    return_json: bool = True,
    result_parsing_func: Parser | None = None,
) -> Response:
    backend = resolve_multimodal_backend(model_name)
    parser = result_parsing_func or get_llm_parser(model_name, return_json, backend)
    model = resolve_multimodal_model_name(model_name)
    if backend == "agy":
        raise NotImplementedError("agy does not support PDF attachments.")
    if backend == "t3":
        raw = call_t3_with_contents(
            prompt_text=_combine_system_and_user(prompt, system_instruction),
            attachments=[pdf_path], model_name=model,
        )
        return _wrap_raw_text_response(raw, parser, check_parsed_response_not_none)
    args = dict(model_name=model, result_parsing_func=parser,
                check_parsed_response_not_none=check_parsed_response_not_none)
    if backend == "openai":
        paper = load_paper(pdf_path, min_size=100)
        return call_openai_models_with_text_prompt(
            prompt=f"Paper Content:\n{paper}\n\nTask:\n{prompt}",
            system_prompt=system_instruction, **args,
        )
    with open(pdf_path, "rb") as file:
        contents = [
            types.Part.from_bytes(data=file.read(), mime_type="application/pdf"),
            types.Part.from_text(text=prompt),
        ]
    config = {}
    if temperature is not None:
        config["temperature"] = temperature
    if system_instruction is not None:
        config["system_instruction"] = system_instruction
    return call_gemini_with_contents(contents=contents, generation_configs=config, **args)
