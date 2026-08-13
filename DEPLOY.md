# Getting a permanent link (Streamlit Community Cloud)

Running `streamlit run app.py` on your laptop only serves the app at `localhost` - that address
only exists on your machine while the command is running, which is why the link stopped working
on a different computer the next day. Streamlit Community Cloud runs the app on Streamlit's own
servers instead, so it gets one link that's always up.

It's free for this kind of app.

## 1. Put the code on GitHub

If you don't already have a GitHub account, create one at github.com (free).

From this folder:

```
git init
git add .
git commit -m "QLD tenement map generator"
```

Then on github.com, create a new **empty** repository (no README/license), and push:

```
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

`.gitignore` is already set up so `.streamlit/secrets.toml` (if you make one locally) and test
output never get pushed.

## 2. Deploy on Streamlit Community Cloud

1. Go to **share.streamlit.io** and sign in with your GitHub account.
2. Click **"New app"**, pick the repo you just pushed, branch `main`, main file `app.py`.
3. Click **Deploy**. First deploy takes a few minutes (installing geopandas/GDAL etc. - the
   included `packages.txt` covers the system libraries geopandas needs).
4. You'll get a permanent link like `https://<something>.streamlit.app` - that one works from
   any computer, any time, whether or not your laptop is even on.

## 3. Add email sending (optional)

If you want the "Send email now" button to actually work, add your SMTP details in the app's
dashboard: **Settings -> Secrets**, and paste in the contents of `.streamlit/secrets.toml.example`
with real values filled in. Never commit real credentials to the GitHub repo itself - the Secrets
manager is the only place they should live.

## Updating the app later

Any time you (or I) change `epm_locality_map.py` or `app.py`, just commit and push:

```
git add .
git commit -m "describe the change"
git push
```

Streamlit Cloud automatically redeploys within a minute or two - no need to touch the dashboard
again.
