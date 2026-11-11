# Job Sweep

Emails David Rodgers a list of new marketing and communications jobs paying $95K+ in DC, Baltimore and Northern Virginia,
every **Tuesday and Thursday at about 8:45am Eastern**. The email looks the same every time, every title links to the
employer's own posting, and no job is ever sent twice.

## How it works

1. **Federal jobs:** taken from the official USAJOBS data feed (public-facing GS-13 to GS-15 jobs in the 1035, 1001, 0301, 1101 and 0343 series).
2. **State, county, university and nonprofit jobs:** found by AI web search (OpenAI or Claude), government first.
3. **Fixed rules, applied in code:** salary ≥ $95K, open now, no aggregator or general careers-page links, recent postings only.
4. **Fit check:** the AI keeps the good matches and writes a one-line *Fit* or *Stretch* note for each.
5. **Link check:** each link is opened; dead or expired postings are dropped.
6. **Repeat check:** jobs in `data/sent_jobs.json` are skipped, and new ones are added after sending.
7. **Email:** sent from your Gmail.

## One-time setup (about 20 minutes)

1. **Create the repository.** On github.com, click **New repository**, name it `job-sweep`, choose **Private**, and click **Create**.
   Then click **uploading an existing file**, drag in everything from this folder (including the `.github` folder), and click **Commit changes**.
   *Tip: on a Mac, press Cmd+Shift+. in Finder to show the hidden `.github` folder.*
2. **Get the keys.**
   - **OpenAI key:** platform.openai.com → API keys → Create new secret key. Your account needs a payment method on file.
   - **USAJOBS key (free):** developer.usajobs.gov → Request API Key. It arrives by email.
   - **Gmail app password:** myaccount.google.com → Security → turn on 2-Step Verification → search "App passwords" → create one named "Job Sweep". Copy the 16 letters.
3. **Add the keys to GitHub.** In the repository, open **Settings → Secrets and variables → Actions → New repository secret**, and add each of these:

   | Name | Value |
   |---|---|
   | `OPENAI_API_KEY` | your OpenAI key |
   | `USAJOBS_API_KEY` | the key from the USAJOBS email |
   | `USAJOBS_EMAIL` | the email address you registered with USAJOBS |
   | `GMAIL_ADDRESS` | readuvall@gmail.com |
   | `GMAIL_APP_PASSWORD` | the 16-letter app password |

   *(Using Claude instead of OpenAI? Add `ANTHROPIC_API_KEY` and set `ai_provider: "anthropic"` in `config.yaml`.)*
4. **Test it.** Open **Actions → Job Sweep → Run workflow**.
   - Tick **Preview only** to build the email without sending it. The preview is under the run's **Artifacts**.
   - Leave it unticked to send a real email. In review mode, that email goes only to you.
5. **Go live.** In `config.yaml`, change `review_mode: true` to `review_mode: false` (click the pencil icon, edit, then **Commit changes**).
   From then on, David gets the emails and you're cc'd.

## Changing things later

Everything lives in `config.yaml`: who gets the emails, minimum salary, keywords, federal locations, David's profile, and which AI is used.
Edit it on github.com; the next run uses the new settings.

## If something breaks

- If a run fails, GitHub emails the account owner. Click into the run to see the log.
- If one source fails (for example, USAJOBS is down), the email still goes out with the rest.
- GitHub can start scheduled runs 5–30 minutes late. That's normal.

## Costs

- GitHub Actions: free for this.
- USAJOBS: free.
- OpenAI: one web-search call and one short review call per run. The cost varies by model; check the Usage page after the first few runs.
