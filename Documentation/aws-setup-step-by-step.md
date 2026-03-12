# AWS setup step-by-step (India region, for Textract)

Use this guide to create an AWS account (if needed), then get credentials so the app can use **Amazon Textract** in the **Mumbai (ap-south-1)** region.

---

## Part 1: AWS account

### Step 1.1 – Sign up (if you don’t have an account)

1. Go to **https://aws.amazon.com** and click **Create an AWS Account**.
2. Enter **email**, choose **AWS account root user**, click **Verify email** and enter the code.
3. Set a **password**, choose **Account name** (e.g. “My Auto”).
4. **Contact information**: choose **Personal** or **Business**, fill required fields.
5. **Payment**: add a **credit/debit card**. AWS will charge only for what you use; Textract has a free tier (e.g. first 1,000 pages/month).
6. **Identity**: complete **phone verification** (call or SMS).
7. **Support plan**: choose **Basic support (free)**.
8. Click **Create account and continue**. Sign in when prompted.

### Step 1.2 – Sign in to the console

1. Go to **https://console.aws.amazon.com**.
2. Sign in with your **root** email and password (or an IAM user once you create one).

---

## Part 2: Create an IAM user and access keys (recommended)

Using the **root** user for day-to-day work is not recommended. Create an **IAM user** with limited permissions (e.g. only Textract).

### Step 2.1 – Open IAM

1. In the AWS Console top search bar, type **IAM** and open **IAM** (Identity and Access Management).
2. Or go directly: **https://console.aws.amazon.com/iam/**

### Step 2.2 – Create a user

1. In the left menu click **Users**.
2. Click **Create user**.
3. **User name**: e.g. `textract-app` (or any name).
4. Leave **Provide user access to the AWS Management Console** **unchecked** (we only need API keys for the app). Click **Next**.
5. **Set permissions**: choose **Attach policies directly**.
6. In the search box type **Textract**, select **AmazonTextractFullAccess**.
7. Click **Next**, then **Create user**.

### Step 2.3 – Create access keys for the user

1. On the **Users** list, click the user you just created (e.g. `textract-app`).
2. Open the **Security credentials** tab.
3. Scroll to **Access keys**.
4. Click **Create access key**.
5. **Use case**: choose **Application running outside AWS** (or “Command Line Interface (CLI)”). Next.
6. (Optional) Add a description tag, e.g. “My Auto Textract”. Next.
7. Click **Create access key**.
8. You’ll see:
   - **Access key ID** (e.g. `AKIA...`)
   - **Secret access key** (shown only once)
9. Click **Download .csv file** (optional but useful), or copy both values to a safe place. You will **not** see the secret again.
10. Click **Done**.

---

## Part 3: Use India (Mumbai) region

- **Region name**: Asia Pacific (Mumbai)  
- **Region code**: **ap-south-1**

You don’t need to “create” the region; it’s already there. When calling Textract from the app, we use **ap-south-1** so requests go to Mumbai.

---

## Part 4: Put credentials in the app

1. Open **`backend/.env`** in your project (same folder as your FastAPI app).
2. Add these lines (paste your real **Access key ID** and **Secret access key**):

   ```env
   AWS_ACCESS_KEY_ID=AKIA......................
   AWS_SECRET_ACCESS_KEY=your_secret_key_here
   AWS_REGION=ap-south-1
   ```

3. **Rules:**
   - No quotes around the values.
   - No spaces before or after `=`.
   - If your secret has `#`, don’t put it inside a comment.

4. Save the file.
5. **Restart your backend** (stop and start uvicorn/FastAPI) so it reloads `.env`.

---

## Part 5: Verify

1. In the app, go to **Add Sales** → **Extracted Information**.
2. Click **Choose file & run Textract** and select a document image (e.g. your Details sheet).
3. If credentials and region are correct, you should see extracted text (or an error from Textract itself, not “unable to locate credentials”).

---

## Quick reference

| Item        | Value        |
|------------|--------------|
| Region     | Asia Pacific (Mumbai) |
| Region code| **ap-south-1** |
| IAM permission | AmazonTextractFullAccess |
| Env vars   | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION=ap-south-1` |

---

## Security notes

- **Never** commit `.env` or share your Secret access key (e.g. in Git or chat).
- `.env` should be in `.gitignore` (it usually is).
- If a key is exposed, go to IAM → Users → your user → Security credentials → **Delete** the access key and create a new one.
