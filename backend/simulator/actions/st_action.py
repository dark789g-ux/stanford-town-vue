#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Desc   : StanfordTown Action
import json
import time
from abc import abstractmethod
from pathlib import Path
from typing import Any, Optional, Union

from core.action import Action
from simulator.utils.const import PROMPTS_DIR
from core.logs import logger


# Chat-tuned models (e.g. DeepSeek V4) wrap replies in conversational preamble
# that consumes the tight max_tokens budget before the actual answer is emitted.
# The original ST prompts assumed completion-style continuation. Floor the budget
# and append a strict-output rule so chat models leave room for the real answer.
GPT35_MIN_MAX_TOKENS = 64
GPT35_STRICT_SUFFIX = (
    "\n\n[Output rule] Reply with ONLY the answer in the shortest form possible. "
    "No preamble, no explanation, no quotation marks, no trailing punctuation."
)


class STAction(Action):
    name: str = "STAction"
    prompt_dir: Path = PROMPTS_DIR
    fail_default_resp: Optional[str] = None

    @property
    def cls_name(self):
        return self.__class__.__name__

    @abstractmethod
    def _func_validate(self, llm_resp: str, prompt: str):
        raise NotImplementedError

    @abstractmethod
    def _func_cleanup(self, llm_resp: str, prompt: str):
        raise NotImplementedError

    @abstractmethod
    def _func_fail_default_resp(self):
        raise NotImplementedError

    def generate_prompt_with_tmpl_filename(self, prompt_input: Union[str, list], tmpl_filename) -> str:
        """
        same with `generate_prompt`
        Args:
            prompt_input: the input we want to feed in (IF THERE ARE MORE THAN ONE INPUT, THIS CAN BE A LIST.)
            tmpl_filename: prompt template filename
        Returns:
            a str prompt that will be sent to LLM server.
        """
        if isinstance(prompt_input, str):
            prompt_input = [prompt_input]
        prompt_input = [str(i) for i in prompt_input]

        f = open(str(self.prompt_dir.joinpath(tmpl_filename)), "r")
        prompt = f.read()
        f.close()
        for count, i in enumerate(prompt_input):
            prompt = prompt.replace(f"!<INPUT {count}>!", i)
        if "<commentblockmarker>###</commentblockmarker>" in prompt:
            prompt = prompt.split("<commentblockmarker>###</commentblockmarker>")[1]
        return prompt.strip()

    async def _aask(self, prompt: str) -> str:
        import time as _time
        from simulator.utils import llm_logger as _llm_logger

        _llm_logger.set_action(self.cls_name)
        t0 = _time.monotonic()
        response: Optional[str] = None
        error: Optional[str] = None

        cm = getattr(self.llm, "cost_manager", None)
        pre_prompt_tokens = getattr(cm, "total_prompt_tokens", 0) if cm else 0
        pre_completion_tokens = getattr(cm, "total_completion_tokens", 0) if cm else 0
        pre_cost = getattr(cm, "total_cost", 0.0) if cm else 0.0

        try:
            response = await self.llm.aask(prompt)
            return response
        except Exception as exc:
            error = repr(exc)
            raise
        finally:
            cfg_llm = getattr(self.config, "llm", None)
            params = {
                "temperature": getattr(cfg_llm, "temperature", None),
                "max_tokens": getattr(cfg_llm, "max_token", None),
            }
            usage = None
            cost_usd = None
            if cm:
                dp = getattr(cm, "total_prompt_tokens", 0) - pre_prompt_tokens
                dc = getattr(cm, "total_completion_tokens", 0) - pre_completion_tokens
                if dp or dc:
                    usage = {"prompt_tokens": dp, "completion_tokens": dc, "total_tokens": dp + dc}
                d_cost = getattr(cm, "total_cost", 0.0) - pre_cost
                if d_cost:
                    cost_usd = round(d_cost, 6)
            _llm_logger.log_call(
                prompt=prompt,
                response=response,
                model=getattr(cfg_llm, "model", None),
                params=params,
                usage=usage,
                cost_usd=cost_usd,
                latency_ms=int((_time.monotonic() - t0) * 1000),
                retry_idx=0,
                used_fail_default=False,
                error=error,
            )

    async def _run_gpt35_max_tokens(self, prompt: str, max_tokens: int = 50, retry: int = 3):
        from simulator.utils import llm_logger as _llm_logger

        strict_prompt = prompt.rstrip() + GPT35_STRICT_SUFFIX
        effective_max_tokens = max(max_tokens, GPT35_MIN_MAX_TOKENS)
        for idx in range(retry):
            try:
                tmp_max_tokens_rsp = getattr(self.config.llm, "max_token", 1500)
                setattr(self.config.llm, "max_token", effective_max_tokens)
                self.llm.use_system_prompt = False

                # _aask logs itself; we tag the latest log record's retry_idx after the fact
                # by writing a second pseudo-record only when validation fails. Simpler: rely on
                # the per-attempt _aask log + a marker record on fail_default below.
                _llm_logger.set_action(self.cls_name)
                llm_resp = await self._aask(strict_prompt)

                setattr(self.config.llm, "max_token", tmp_max_tokens_rsp)
                logger.info(f"Action: {self.cls_name} llm _run_gpt35_max_tokens raw resp: {llm_resp}")
                if self._func_validate(llm_resp, prompt):
                    return self._func_cleanup(llm_resp, prompt)
                # validation failed → record retry index marker
                _llm_logger.log_call(
                    prompt="", response=llm_resp, model=getattr(self.config.llm, "model", None),
                    params={"max_tokens": effective_max_tokens},
                    usage=None, cost_usd=None, latency_ms=0,
                    retry_idx=idx, used_fail_default=False,
                    error="func_validate returned False",
                )
            except Exception as exp:
                logger.warning(f"Action: {self.cls_name} _run_gpt35_max_tokens exp: {exp}")
                _llm_logger.log_call(
                    prompt="", response=None, model=getattr(self.config.llm, "model", None),
                    params={"max_tokens": effective_max_tokens},
                    usage=None, cost_usd=None, latency_ms=0,
                    retry_idx=idx, used_fail_default=False,
                    error=repr(exp),
                )
                time.sleep(5)
        _llm_logger.log_call(
            prompt="", response=self.fail_default_resp,
            model=getattr(self.config.llm, "model", None),
            params={}, usage=None, cost_usd=None, latency_ms=0,
            retry_idx=retry, used_fail_default=True, error=None,
        )
        return self.fail_default_resp

    async def _run_gpt35(
        self, prompt: str, example_output: str, special_instruction: str, retry: int = 3
    ) -> Union[bool, Any]:
        """same with `gpt_structure.ChatGPT_safe_generate_response`"""
        prompt = '"""\n' + prompt + '\n"""\n'
        prompt += f"Output the response to the prompt above in json. {special_instruction}\n"
        prompt += "Example output json:\n"
        prompt += '{"output": "' + str(example_output) + '"}'

        for idx in range(retry):
            try:
                llm_resp = await self._aask(prompt)
                logger.info(f"Action: {self.cls_name} llm _run_gpt35 raw resp: {llm_resp}")
                end_idx = llm_resp.strip().rfind("}") + 1
                llm_resp = llm_resp[:end_idx]
                llm_resp = json.loads(llm_resp)["output"]

                if self._func_validate(llm_resp, prompt):
                    return self._func_cleanup(llm_resp, prompt)
            except Exception as exp:
                logger.warning(f"Action: {self.cls_name} _run_gpt35 exp: {exp}")
                time.sleep(5)  # usually avoid `Rate limit`
        return False

    async def _run_gpt35_wo_extra_prompt(self, prompt: str, retry: int = 3) -> str:
        for idx in range(retry):
            try:
                llm_resp = await self._aask(prompt)
                llm_resp = llm_resp.strip()
                logger.info(f"Action: {self.cls_name} llm _run_gpt35_wo_extra_prompt raw resp: {llm_resp}")
                if self._func_validate(llm_resp, prompt):
                    return self._func_cleanup(llm_resp, prompt)
            except Exception as exp:
                logger.warning(f"Action: {self.cls_name} _run_gpt35_wo_extra_prompt exp: {exp}")
                time.sleep(5)  # usually avoid `Rate limit`
        return self.fail_default_resp

    async def run(self, *args, **kwargs):
        """Run action"""
        raise NotImplementedError("The run method should be implemented in a subclass.")
