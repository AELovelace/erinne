# Signing in and out

## Your account

You sign in with a **Mnemerynne username and password**. An admin creates the account and hands you the first password; change it on [Preferences](preferences.md#password) afterwards.

Your account may be **linked to your domain (Windows) account**. That link is what gives you access to libraries stored on the file share and, if your organisation uses them, to admin rights, Erynne's tools and internal systems. You still sign in with your Mnemerynne password; the link only tells Mnemerynne who you are on the network. Preferences shows whether your account is linked and which groups you are in.

## Signing in

1. Open Erynne's address in a browser (your admin gives you the address, for example `https://erynne.example.internal`).
2. Enter your username and password.

The sign-in page uses the theme the browser last used (Sakura or Neon); you can change the theme on Preferences once signed in.

## Wrong passwords and lockouts

After **5 wrong passwords** for a username from the same computer, sign-in for that username is blocked for **5 minutes**. Many failures across different usernames from one computer block that computer for 5 minutes too. Wait it out; nothing needs resetting. If you have forgotten your password, ask an admin to reset it (Access → Accounts).

Changing your own password also requires the current one, and wrong guesses there count towards the same lockout.

## Staying signed in

A browser stays signed in for **24 hours** since you last signed in; after that you sign in again. Closing the browser does not sign you out.

## Signing out

- **Sign out** (top right) ends your session everywhere: this browser and any other you were still signed in on (a shared front-desk PC, a phone). Nobody can carry on with a copy of a session you have ended.
- **Sign out other browsers** on Preferences ends every other session (a shared front-desk PC, a phone) while this one stays signed in. Changing your password does the same.
- Connections from Claude are separate: see [Claude](claude.md) for tokens and connections.

## When an admin changes something

- A password reset by an admin signs you out of every browser.
- If an admin turns off "Can sign in" for your account, every session ends at once and Claude connections stop working.
