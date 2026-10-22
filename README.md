# Mouse Finder

A personal, read-only watcher for **medium Finalmouse ULX Prophecy or Sakura** listings on r/MouseMarket. It reads Reddit's public RSS feed; it needs **Python 3.10+**, with no packages, API key, Reddit password, or login.

The feed returned HTTP 200 and 100 recent posts from this computer on October 22, 2026. This verifies current access, not future availability. Reddit can change or restrict RSS. Logged-out RSS currently allows about **one request per minute** (`x-ratelimit-remaining` drops to 0 after a single request). One page of 100 posts covers about six days of MouseMarket, so a normal check makes one request. If more pages are needed, the script waits for the reset time Reddit reports, up to two minutes. If an older page is still refused, it keeps the newer posts and warns that coverage may be incomplete. On HTTP 401/403/429 for the first page, the script reports a failed check, exits with an error, and leaves the previous report intact. It does not switch hosts or attempt to bypass restrictions.

## Run it

In PowerShell:

```powershell
cd $HOME\mouse-finder
python .\mouse_finder.py --open
```

This fetches recent posts and opens `matches.html`, a local report with listing links. It looks back 48 hours and requests up to five pages of 100 posts, stopping once it reaches the time boundary. Repeated checks recognize already seen matches. Updated matching descriptions can appear as new/updated results. For automatic checks with phone alerts, see [Phone alerts](#phone-alerts), then [Cloud checks with GitHub Actions](#cloud-checks-with-github-actions) (runs while your computer is off) or [Scheduled checks on Windows](#scheduled-checks-on-windows).

```powershell
# Only show Prophecy/Sakura (including Clix/Tfue aliases) + medium matches.
python .\mouse_finder.py --strict --open

# Suitable for a recurring task: open the report only when something changes.
python .\mouse_finder.py --open-on-new

# Shorter lookback; at most two feed requests.
python .\mouse_finder.py --hours 24 --max-pages 2
```

## How filtering works

- Check the **[H] (have)** section and require a payment method in **[W] (want)**: `PayPal`, `PP`, `PPG&S`, `G&S`, `Cash`, `CashApp`, `Venmo`, `Zelle`, `Money`, or `$`. A buyer with `[H] PayPal [W] ULX Sakura` does not qualify. Trades for another mouse are excluded.
- When the title is generic, such as `[H] Finalmouse collection, Lamzu [W] PayPal`, read the inventory in the body. Skip this when `[H]` starts with a payment (`[H] PayPal`, `[H] $`) or `[W]` names the mouse, since then the body describes what the poster wants.
- Recognize `ULX`, `UltralightX`, `Ultralight X`, and `Ultralight-X`, case-insensitively.
- Recognize all four **Prophecy colorways: Clix, ScreaM, Tarik, and Tfue**, case-insensitively, alongside the **Prophecy** and **Sakura** edition names. `Clix` and `Tfue` identify Prophecy even without `ULX` or `Prophecy`, such as `[H] Clix M [W] PayPal`. `Tarik` and `ScreaM` without an edition are **Possible matches** because these names can refer to older mice. Explicit `Pro`, `Starlight`, and `Scream One` listings remain excluded.
- Recognize `Medium`, `Med`, `M`, and `Lion`. `Classic`, `Tiger`, `Large`, `Small`, and `Cheetah` are different sizes. Size matching is per inventory fragment or table row, rather than anywhere in the post. Apostrophes are not sizes: the `s` in "it's" or "Tfue's" is not Small, and the `m` in "I'm" is not Medium.
- A **Strong match** identifies Prophecy/Sakura by edition name or a Clix/Tfue alias, and explicitly states medium size. A **Possible match** has an ambiguous edition, omits the size, or lists multiple sizes. Possible matches are included by default so vague listings are not silently discarded; `--strict` hides them.
- Exclude explicit older/other editions such as Phantom, Aceu, Pro, Competition, and Frostlord when the row does not explicitly identify Prophecy or Sakura. These edition choices follow your requested scope.
- Use body rows to detect sold/pending/struck-out items. A struck-out quantity, such as `~~2~~ 1`, does not itself mean the item is sold.

**Your pasted example does not qualify**: its medium Phantom has a different chassis, and its Pro. Scream/Tarik listings are earlier editions and Classic size. It contains no explicit medium Prophecy or Sakura.

The RSS feed includes titles and HTML post bodies, including tables, but **does not expose sale flair**. This version therefore uses the `[H] ... [W] ...` convention. It cannot reliably detect flair-only closures, status updates in comments, photo-only details, typos, or every ambiguous inventory layout. All matches require a quick look at the original listing. The report is a snapshot, not a live view of inventory.

## Phone alerts

With `--ntfy-topic`, each new or updated match is pushed to your phone through [ntfy](https://ntfy.sh), a free push service that needs no account. Tapping the notification opens the listing. Strong matches use high priority and are sent first. A check sends at most five match pushes, plus one summary if there are more. If a push fails, that match stays unseen and the next check sends it again.

1. Install **ntfy** from the Play Store or App Store.
2. Tap **+**, then subscribe to your topic on the default server, `ntfy.sh`. Use a long random name, for example from `python -c "import secrets; print('mouse-finder-' + secrets.token_hex(8))"`. Keep it out of the repository: the cloud checks read it from a GitHub secret, and the Windows task stores it in its arguments.

3. Send a test push:

   ```powershell
   python .\mouse_finder.py --ntfy-topic YOUR-TOPIC --test-notification
   ```

**Anyone who knows the topic name can read or send its notifications**, so the topic is a long random name. Pushes contain only the public listing title, the matching rows, and the link.

If **six checks in a row fail** (about two hours in the cloud), you get one "Mouse Finder checks are failing" push. Otherwise, a blocked feed would look the same as a quiet market. You get a "working again" push after the next successful check.

## Cloud checks with GitHub Actions

[`.github/workflows/watch.yml`](.github/workflows/watch.yml) runs a check **every 20 minutes from 8:00 AM to 1:00 AM Pacific** on GitHub's servers, so it works while your computer is off. Scheduled workflows are free in public repositories.

- **Time window:** GitHub schedules use UTC, so the workflow is scheduled for the hours that cover both daylight and standard time. Its first step skips any run outside 8:00 AM–1:10 AM Pacific.
- **Timing:** GitHub often starts scheduled runs a few minutes late, and can skip runs when it is busy. The 48-hour lookback still catches listings from a skipped run on the next one.
- **Topic:** the ntfy topic is the `NTFY_TOPIC` repository secret (**Settings → Secrets and variables → Actions**). It never appears in the code or logs.
- **State:** it lives on a separate `state` branch, as `state.json`. The workflow commits it only when something changes: a new match, or a change in the failure count. The file holds only anonymous digests.
- **Failed checks:** a blocked or failed check doesn't turn the run red. The failure push above covers outages, which avoids a GitHub failure email every 20 minutes. A setup problem that ntfy can't report, such as a missing or malformed `NTFY_TOPIC` secret, does turn the run red, and GitHub emails you.
- **Setting the secret from PowerShell:** use `gh secret set NTFY_TOPIC --body YOUR-TOPIC`. Piping the topic into `gh secret set` adds an invisible line break, and the script rejects the topic.
- **Keepalive:** GitHub disables schedules in public repositories after 60 days without activity. A weekly job re-enables the workflow through GitHub's API. If GitHub ever disables it anyway, enable it again under **Actions → Watch MouseMarket**.
- **Manual run:** use **Actions → Watch MouseMarket → Run workflow**. Manual runs ignore the time window.

Use either the cloud checks or the Windows task, not both. Each keeps its own record of sent matches, so running both pushes every match twice.

## Scheduled checks on Windows

This is an alternative to the cloud checks, and it is **disabled** while the cloud checks are in use. To switch back, disable the workflow under **Actions**, then run `Enable-ScheduledTask -TaskName "Mouse Finder"`.

A Task Scheduler task named **Mouse Finder** runs a check **every 15 minutes from 8:00 AM to 1:00 AM** on this computer's clock, which is Pacific time. Daylight-saving changes don't shift the window. The task uses `pythonw.exe`, so no console window appears, and appends one line per run to `watcher.log`.

It runs only while the computer is on and you are signed in to Windows. A locked screen is fine, but signing out, shutting down, or sleeping stops the checks. If the computer was off at 8:00 AM, a missed check runs as soon as it is on again. Each check is one feed request (69 a day), and each one looks back 48 hours. That overlap covers the overnight gap and any time the computer was off.

To recreate or change it, run this in PowerShell, replacing `YOUR-TOPIC`. The start time must have no time-zone offset, or the window shifts an hour when daylight saving changes.

```powershell
$action = New-ScheduledTaskAction -Execute "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe" -Argument "`"$HOME\mouse-finder\mouse_finder.py`" --ntfy-topic YOUR-TOPIC --log `"$HOME\mouse-finder\watcher.log`"" -WorkingDirectory "$HOME\mouse-finder"
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 8:00AM -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 17 -Minutes 1)).Repetition
$trigger.Repetition.StopAtDurationEnd = $false
$trigger.StartBoundary = (Get-Date -Hour 8 -Minute 0 -Second 0).ToString("yyyy-MM-ddTHH:mm:ss")
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Mouse Finder" -Action $action -Trigger $trigger -Settings $settings -Force
```

To pause or remove it:

```powershell
Disable-ScheduledTask -TaskName "Mouse Finder"
Unregister-ScheduledTask -TaskName "Mouse Finder"
```

## Local files and verification

`matches.html` is replaced after each successful fetch. `.mouse-finder-state.json` stores anonymous match digests for deduplication, expiring after 30 days, plus a count of consecutive failed checks. It contains no post titles, bodies, or usernames. Delete or rename the state file to treat current matches as new again. `watcher.log` holds one line per scheduled run, including failures and warnings.

```powershell
python -m unittest -v
```

Offline parsing is supported for a saved Atom feed:

```powershell
python .\mouse_finder.py --input .\feed.xml --report .\offline-report.html --state .\offline-state.json
```

## Sources

- [MouseMarket title-format rules](https://www.reddit.com/r/MouseMarket/about/rules.json)
- [Reddit listing documentation, including RSS support](https://www.reddit.com/dev/api/#GET_new)
- [MouseMarket RSS feed](https://www.reddit.com/r/MouseMarket/new/.rss?limit=100)
- [Finalmouse Prophecy sizing](https://finalmouse.com/products/ulx-prophecy-scream)
- [Finalmouse catalog listing all four Prophecy colorways](https://finalmouse.com/collections/all?page=2)
- [Finalmouse Sakura](https://finalmouse.com/products/ulx-sakura)
- [Reddit's API access requirements](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- [Reddit's August 2026 announcement about restricting new API requests](https://redditinc.com/news/modernizing-reddits-infrastructure-and-moderation-tools)
