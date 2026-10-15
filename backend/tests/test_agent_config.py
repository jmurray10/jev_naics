from pathlib import Path

from jev_naics._jev.agent_config import load_agent

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agents" / "naics" / "agent.yaml"


def test_the_naics_agent_emits_one_industry_or_a_code_owned_hold() -> None:
    agent = load_agent(AGENT)
    assert agent.name == "naics"
    assert agent.eval_path.resolve().is_file()
