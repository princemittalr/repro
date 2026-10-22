# Repro setup guide (Phase 0)

Work through these in order. Each section ends with a check so you know it worked.
Web dashboards change often; if a button is named slightly differently, look for the closest match.

## 1. Nebius Token Factory account and API key

1. Open https://tokenfactory.nebius.com and click **Sign in**.
2. Choose **Continue with GitHub** (recommended, since we use GitHub anyway) or Google.
3. Accept the terms. You land on the Token Factory dashboard / playground.
4. In the left sidebar (or your profile menu, top right) open **API keys**.
5. Click **Create API key**, name it `repro-dev`, and click **Create**.
6. **Copy the key now.** It is shown only once. Paste it into your `.env` as `NEBIUS_API_KEY=...`.
7. Billing: if the console asks for a payment method before you can make calls, open **Billing**,
   add a card, and keep the balance at the free credits (step 2 below). We do not need to top up.

Check: `python -m scripts.list_models` prints a list of models (see the main commands).

## 2. Claim the free credits

1. Hackathon credits ($25): open https://nebiusglobalaihackathon.devpost.com/resources, find the
   Nebius credits / promo form, and submit it with activation code **`NEBIUS-DEVPOST-GLOBAL26`**.
   Use the **same email** as your Token Factory account.
2. Builders Program ($25 + Tavily credits): open https://dev.nebius.com/builders, click **Join** /
   **Apply**, sign in with the same account, and complete the form (project: "Repro, autonomous bug
   reproduction and fixing agent for the Nebius x NVIDIA hackathon").
3. Check: Token Factory **Billing** page shows the credit balance.
4. If credits don't show after a few hours: join the Nebius Discord (link on the Devpost Resources
   page), post in the hackathon/support channel with your account email and the code you used.
   Do not post your API key anywhere.

## 3. Tavily account and API key

1. Open https://app.tavily.com and sign up (GitHub or Google).
2. The dashboard shows a default key starting with `tvly-`. Click the copy icon.
3. Paste it into `.env` as `TAVILY_API_KEY=tvly-...`.
4. Free tier gives 1,000 searches/month, plenty for us. Builders Program credits are a bonus.

Check: `python -m scripts.check_keys` prints `PASS  Tavily search works`.

## 4. GitHub

### 4a. Account and Git identity
1. Create an account at https://github.com if you don't have one.
2. In a terminal:
   ```
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```
   Use the same email as your GitHub account (or your GitHub no-reply email).

### 4b. Create the two repos now (empty is fine)
1. https://github.com/new → name `repro`, **Public**, no README (we push ours). Click **Create repository**.
2. https://github.com/new → name `shoplite`, **Public**, tick **Add a README** (so it has a main branch). Click **Create repository**.
3. Put `GITHUB_DEMO_REPO=your-username/shoplite` in `.env`.

### 4c. Fine-grained Personal Access Token (only for shoplite)
1. GitHub → your avatar (top right) → **Settings** → **Developer settings** (bottom of left sidebar)
   → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Token name: `repro-agent`. Expiration: **30 days**.
3. Repository access: **Only select repositories** → pick `shoplite`.
4. Permissions → **Repository permissions**:
   - **Contents: Read and write** (push the fix branch)
   - **Pull requests: Read and write** (open the PR)
   - **Metadata: Read-only** (added automatically)
   Leave everything else as "No access".
5. Click **Generate token**, copy it (starts with `github_pat_`), paste into `.env` as `GITHUB_TOKEN=...`.

Check: `python -m scripts.check_keys` prints `PASS  GitHub token works` and `PASS  Demo repo reachable`.

## 5. Tools

You need: Python 3.11+, Node.js LTS, Git, ffmpeg, VS Code, and Playwright's Chromium.
Your coach gives you the exact install commands for your OS. Verify each:

| Tool | Verify command | Expected |
|------|----------------|----------|
| Python | `python --version` (Windows) / `python3 --version` | `Python 3.11.x` or newer |
| Node.js | `node --version` | `v22.x` or newer LTS |
| Git | `git --version` | `git version 2.x` |
| ffmpeg | `ffmpeg -version` | first line starts `ffmpeg version` |
| VS Code | `code --version` | a version number |
| Chromium | `python -m playwright install chromium` | downloads, then finishes without error |

## 6. The .env file

Copy `.env.example` to `.env` and fill in every value. `.env` is in `.gitignore`, so it never reaches GitHub.
Model IDs: run `python -m scripts.list_models`; every line under "Routing check" should say `OK`.

## 7. Accounts for later (create now, takes 2 minutes)

- **Render** (https://render.com) or **Railway** (https://railway.app): sign up with GitHub. Used in Phase 5 for the demo URL.
- **YouTube**: any Google account; open https://studio.youtube.com once to create your channel. Used in Phase 7.
