# ไฟล์สำรอง: เนื้อหาไฟล์ .github/workflows/signal.yml (ตัวจับเวลาทุก 5 นาที)

ไฟล์นี้เป็นเพียง "สำเนาฉุกเฉิน" ระบบไม่ได้ใช้งานมันโดยตรง
ใช้เฉพาะกรณี: อัปโหลดโค้ดขึ้น GitHub แล้ว "ไม่เห็นโฟลเดอร์ .github" ในหน้า repo
(เพราะโฟลเดอร์ที่ขึ้นต้นด้วยจุดถูกซ่อนไว้ในบางเครื่อง)

วิธีแก้: สร้างไฟล์นี้ด้วยมือบน GitHub 1 ครั้ง
1. ในหน้า repo กด Add file > Create new file
2. พิมพ์ชื่อไฟล์ว่า: .github/workflows/signal.yml  (พิมพ์เครื่องหมายทับ / แล้ว GitHub จะสร้างโฟลเดอร์ให้เอง)
3. คัดลอกเนื้อหาในกรอบด้านล่างทั้งหมด วางลงไป แล้วกด Commit changes
4. ลบไฟล์ _WORKFLOW_BACKUP.md ทิ้งได้ (ไม่จำเป็นแล้ว)

------------------------------------------------------------------------

name: signal-scan

# ตรวจตลาดทุก 5 นาทีตลอด 24 ชม. บนคลาวด์ GitHub ฟรี - ไม่ต้องเปิดคอมทิ้งไว้
# หมายเหตุ: cron ของ GitHub อาจเริ่มช้ากว่าตาราง 1-3 นาทีในบางช่วง ซึ่งไม่กระทบจังหวะ 15m

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: signal-scan
  cancel-in-progress: false

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Scan market and send signals
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
        run: python main.py

      - name: Persist state (cooldown + daily counter)
        run: |
          if [[ -n "$(git status --porcelain state/)" ]]; then
            git config user.name "trap-hunter-bot"
            git config user.email "bot@users.noreply.github.com"
            git add state/
            git commit -m "state: update cooldowns"
            git push
          fi
