import asyncio
from playwright.async_api import async_playwright
from anthropic import Anthropic
from dotenv import load_dotenv
import os

load_dotenv()
client = Anthropic()

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        await page.goto("https://example.com")
        screenshot_path = "screenshots/test_login.png"
        await page.screenshot(path=screenshot_path)
        
        with open(screenshot_path, "rb") as f:
            image_data = f.read()
            import base64
            encoded = base64.b64encode(image_data).decode("utf-8")
        
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": encoded
                        }
                    },
                    {
                        "type": "text",
                        "text": "You are a QA analyst. Describe what you see on this page and whether it looks correct."
                    }
                ]
            }]
        )
        
        print(response.content[0].text)
        print(f"\n--- Token Usage ---")
        print(f"Input tokens:  {response.usage.input_tokens}")
        print(f"Output tokens: {response.usage.output_tokens}")
        print(f"Total tokens:  {response.usage.input_tokens + response.usage.output_tokens}")
        await browser.close()

asyncio.run(run())