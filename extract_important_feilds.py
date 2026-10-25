import asyncio
import json
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import sys
import os
from dotenv import load_dotenv
import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError, EndpointConnectionError
import time

class Feild_extractor:
    def __init__(self) -> None:
        self.bedrock_apikey = None
        self.client = None
        self.load_env()
        self.setup_bedrock()
            
    def load_env(self):
        load_dotenv()
        if os.getenv("AWS_BEARER_TOKEN_BEDROCK") is None:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] ="ABSKQmVkcm9ja0FQSUtleS16cW9mLWF0LTU4MjE2NTkzMDQ2Nzp4U2lUNjZCWERiYitLRkpTUFBnWml1U0x4V05iQk5GUHE1UkIxZXNPL3QvY3ZpVlRlbHA2OEJvRGJSVT0="
        self.bedrock_apikey = os.getenv("AWS_BEARER_TOKEN_BEDROCK", "ABSKQmVkcm9ja0FQSUtleS16cW9mLWF0LTU4MjE2NTkzMDQ2Nzp4U2lUNjZCWERiYitLRkpTUFBnWml1U0x4V05iQk5GUHE1UkIxZXNPL3QvY3ZpVlRlbHA2OEJvRGJSVT0=")

    def setup_bedrock(self):
        self.client = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            config=Config(
                read_timeout=180,
                connect_timeout=20,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    async def verify_application_fields_visible(self, page) -> dict:
        """Verify whether application form fields are currently visible.
        Does not click buttons or modify the page.
        """
        try:
            results = []
            for frame in page.frames:
                if self.is_ignorable_frame(frame):
                    continue

                try:
                    fields = frame.locator(
                        'input:visible, textarea:visible, select:visible, '
                        '[role="textbox"]:visible, [role="combobox"]:visible'
                    )

                    count = await fields.count()
                    field_details = []

                    for i in range(min(count, 50)):
                        field = fields.nth(i)

                        try:
                            field_type = await field.get_attribute("type") or ""
                            field_name = (
                                await field.get_attribute("name")
                                or await field.get_attribute("placeholder")
                                or await field.get_attribute("aria-label")
                                or ""
                            )

                            field_details.append({
                                "type": field_type,
                                "name": field_name
                            })
                        except Exception:
                            continue

                    if count > 0:
                        results.append({
                            "frame_url": frame.url,
                            "visible_fields": len(field_details),
                            "fields": field_details
                        })

                except Exception:
                    continue

            total_fields = sum(
                item["visible_fields"] for item in results
            )

            return {
                "verified": total_fields > 0,
                "status": (
                    "FIELDS_VISIBLE"
                    if total_fields > 0
                    else "NO_VISIBLE_FIELDS"
                ),
                "total_visible_fields": total_fields,
                "frames": results
            }

        except Exception as exc:
            return {
                "verified": False,
                "status": "VERIFICATION_ERROR",
                "error": str(exc)
            }     
        
    async def discover_form(self, page):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception:
            pass
        
        