# Gmail draft setup

This setup is for one personal Gmail account. The application starts in draft-only mode and does not send messages.

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project or select an existing personal project.
3. Open **APIs & Services → Library**, search for **Gmail API**, and enable it.
4. Open **APIs & Services → OAuth consent screen**.
5. Choose **External** if required, provide the basic app information, add your own Gmail address as a test user, and save the required consent-screen fields.
6. Open **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
7. Choose **Desktop app**, create the client, and download the JSON file.
8. Rename the downloaded file to `credentials.json` and place it in the project root:

   `D:\Career Intelligence Project\career-intelligence-api\credentials.json`

9. Install dependencies with `python -m pip install -r requirements.txt`.
10. Run the manual draft test described below. A browser opens for Google sign-in and consent.
11. Approve the requested Gmail compose permission for the personal account.
12. Confirm `token.json` appears in the project root after authentication.
13. Confirm both `credentials.json` and `token.json` are ignored by Git with `git check-ignore credentials.json token.json`.

The only requested OAuth scope is `https://www.googleapis.com/auth/gmail.compose`, which supports Gmail draft creation and the later explicitly enabled send operation.

## Safe manual draft test

Use an email address you control and two safe sample DOCX files:

```powershell
python test_gmail_draft.py your-address@example.com .\Resume.docx .\CoverLetter.docx
```

The command creates a Gmail draft and prints its draft ID. It never sends the message.
