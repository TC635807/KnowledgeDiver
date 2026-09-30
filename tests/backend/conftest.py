"""测试环境固定：不让 .env 中的实验开关影响单元测试。"""
import os

os.environ["AGENT_EVAL_MODE"] = "full"
os.environ["AGENT_EVAL_MAX_CARDS"] = "0"
os.environ["AGENT_EVAL_MIN_CARDS"] = "0"
