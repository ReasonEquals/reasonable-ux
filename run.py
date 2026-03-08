import asyncio
import argparse
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))

def parse_args():
    parser = argparse.ArgumentParser(description="reasons-qagent orchestrator")
    parser.add_argument("--url", type=str, help="Target URL to test")
    parser.add_argument("--goal", type=str, help="Test goal for the agent")
    parser.add_argument("--steps", type=int, default=10, help="Max steps (default: 10)")
    parser.add_argument("--plan", action="store_true", help="Run planner first to generate test cases")
    return parser.parse_args()

async def run_with_plan(url, steps):
    from planner import plan
    from agent_test import run

    test_plan = await plan(url)

    high_priority = [tc for tc in test_plan["suggested_test_cases"] if tc["priority"] == "high"]
    candidates = high_priority or test_plan["suggested_test_cases"]
    chosen = candidates[0]["goal"]

    print(f"\n🎯 Selected goal: {chosen}\n")

    total_tokens = await run(url=url, goal=chosen, max_steps=steps)
    return total_tokens

async def run_without_plan(url, goal, steps):
    from agent_test import run
    total_tokens = await run(url=url, goal=goal, max_steps=steps)
    return total_tokens

def build_index():
    subprocess.run(["python", "build_index.py"])
    print("📊 Dashboard index updated.")

if __name__ == "__main__":
    args = parse_args()

    url = args.url or "https://the-internet.herokuapp.com/login"
    goal = args.goal or "Test the login form with valid and invalid credentials."

    print(f"\n🚀 Starting test run")
    print(f"   URL:   {url}")
    print(f"   Steps: {args.steps}")
    if args.plan:
        print(f"   Mode:  Planner → Agent\n")
    else:
        print(f"   Goal:  {goal}\n")

    if args.plan:
        total_tokens = asyncio.run(run_with_plan(url, args.steps))
    else:
        total_tokens = asyncio.run(run_without_plan(url, goal, args.steps))

    build_index()

    if total_tokens:
        print(f"\n📊 Total tokens used this run:")
        print(f"   Input:  {total_tokens['input']:,}")
        print(f"   Output: {total_tokens['output']:,}")
        print(f"   Total:  {total_tokens['total']:,}")

    print("\n✅ Run complete. Open dashboard to view results.")