import asyncio
import argparse
import subprocess
from datetime import datetime

def parse_args():
    parser = argparse.ArgumentParser(description="reasons-qagent orchestrator")
    parser.add_argument("--url", type=str, help="Target URL to test")
    parser.add_argument("--goal", type=str, help="Test goal for the agent")
    parser.add_argument("--steps", type=int, default=8, help="Max steps (default: 8)")
    return parser.parse_args()

def run_agent(url, goal, steps):
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))
    
    from agent_test import run
    asyncio.run(run(url=url, goal=goal, max_steps=steps))

def build_index():
    subprocess.run(["python", "build_index.py"])
    print("📊 Dashboard index updated.")

if __name__ == "__main__":
    args = parse_args()
    
    url = args.url or "https://the-internet.herokuapp.com/login"
    goal = args.goal or "Test the login form with valid and invalid credentials."
    
    print(f"\n🚀 Starting test run")
    print(f"   URL:   {url}")
    print(f"   Goal:  {goal}")
    print(f"   Steps: {args.steps}\n")
    
    run_agent(url, goal, args.steps)
    build_index()
    
    print("\n✅ Run complete. Open dashboard to view results.")