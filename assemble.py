"""
交互式装配向导入口。

    python assemble.py

在终端里通过问答完成一台数字员工的装配：选载荷（或生成新载荷骨架）、
选编排与推理模式、挑知识注入时机、定失败契约与权限边界，
最后产出一份可直接运行的装配脚本。
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from agent_chassis.wizard import main

if __name__ == "__main__":
    sys.exit(main(ROOT))
