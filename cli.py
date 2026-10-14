#!/usr/bin python3

import sys
import os
import argparse
import configparser
import asyncio

from sources.llm_provider import Provider
from sources.interaction import Interaction
from sources.agents import Agent, CoderAgent, CasualAgent, FileAgent, PlannerAgent, BrowserAgent, McpAgent
from sources.browser import Browser, create_driver
from sources.utility import pretty_print

import warnings
warnings.filterwarnings("ignore")



# --- 强制设置 HuggingFace 的镜像和缓存路径 ---
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = r"E:\CodeFile\HF_Cache"

# --- 强制清除所有可能干扰的代理设置 ---
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

config = configparser.ConfigParser()
config.read('config.ini')

async def main():
    pretty_print("Initializing...", color="status")
    stealth_mode = config.getboolean('BROWSER', 'stealth_mode')
    personality_folder = "jarvis" if config.getboolean('MAIN', 'jarvis_personality') else "base"
    languages = config["MAIN"]["languages"].split(' ')

    provider = Provider(provider_name=config["MAIN"]["provider_name"],
                        model=config["MAIN"]["provider_model"],
                        server_address=config["MAIN"]["provider_server_address"],
                        is_local=config.getboolean('MAIN', 'is_local'))
    if config.getboolean('RECOVERY', 'fallback_enabled', fallback=False):
        fallback_provider = Provider(
            provider_name=config['RECOVERY']['fallback_provider_name'],
            model=config['RECOVERY']['fallback_provider_model'],
            server_address=config['RECOVERY']['fallback_provider_server_address'],
            is_local=False,
        )
        provider.set_fallback_provider(fallback_provider)
    provider.retry_policy.max_attempts = config.getint('RECOVERY', 'model_max_attempts', fallback=3)

    browser = Browser(
        create_driver(headless=config.getboolean('BROWSER', 'headless_browser'), stealth_mode=stealth_mode, lang=languages[0]),
        anticaptcha_manual_install=stealth_mode
    )

    agents = [
        CasualAgent(name=config["MAIN"]["agent_name"],
                    prompt_path=f"prompts/{personality_folder}/casual_agent.txt",
                    provider=provider, verbose=False),
        CoderAgent(name="coder",
                   prompt_path=f"prompts/{personality_folder}/coder_agent.txt",
                   provider=provider, verbose=False),
        FileAgent(name="File Agent",
                  prompt_path=f"prompts/{personality_folder}/file_agent.txt",
                  provider=provider, verbose=False),
        BrowserAgent(name="Browser",
                     prompt_path=f"prompts/{personality_folder}/browser_agent.txt",
                     provider=provider, verbose=False, browser=browser),
        PlannerAgent(name="Planner",
                     prompt_path=f"prompts/{personality_folder}/planner_agent.txt",
                     provider=provider, verbose=False, browser=browser),
        #McpAgent(name="MCP Agent",
        #            prompt_path=f"prompts/{personality_folder}/mcp_agent.txt",
        #            provider=provider, verbose=False), # NOTE under development
    ]
    planner = next((agent for agent in agents if agent.type == 'planner_agent'), None)
    if planner is not None:
        planner.max_step_retries = config.getint('RECOVERY', 'node_max_retries', fallback=1)
        planner.max_replans = config.getint('RECOVERY', 'workflow_max_replans', fallback=2)
        planner.max_total_steps = config.getint('RECOVERY', 'workflow_max_steps', fallback=24)

    interaction = Interaction(agents,
                              tts_enabled=config.getboolean('MAIN', 'speak'),
                              stt_enabled=config.getboolean('MAIN', 'listen'),
                              recover_last_session=config.getboolean('MAIN', 'recover_last_session'),
                              langs=languages
                            )
    try:
        while interaction.is_active:
            interaction.get_user()
            if await interaction.think():
                interaction.show_answer()
                interaction.speak_answer()
    except Exception as e:
        if config.getboolean('MAIN', 'save_session'):
            interaction.save_session()
        raise e
    finally:
        if config.getboolean('MAIN', 'save_session'):
            interaction.save_session()

if __name__ == "__main__":
    asyncio.run(main())
