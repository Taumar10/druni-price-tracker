# Druni Price Tracker

每天自动检查 Druni 商品价格；如果降价或达到目标价，会通过 Telegram 通知你。

## 1. 修改 products.csv

把 `products.csv` 改成你要监控的商品：

```csv
name,url,target_price
香水A,https://www.druni.es/你的商品链接,29.99
面霜B,https://www.druni.es/你的商品链接,
```

- `name`：你自己给商品起的名字
- `url`：Druni 商品页链接
- `target_price`：目标价，可留空

## 2. 设置 Telegram 通知

### 创建 Bot
1. Telegram 搜索 `@BotFather`
2. 发送 `/newbot`
3. 得到 `TELEGRAM_BOT_TOKEN`

### 获取 Chat ID
1. 给你的 bot 发一句话
2. 打开这个地址，把 `<TOKEN>` 换成你的 token：
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 找到里面的 `chat.id`

## 3. 在 GitHub 设置 Secrets

进入你的 GitHub 仓库：

`Settings → Secrets and variables → Actions → New repository secret`

添加：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 4. 自动运行

`.github/workflows/druni-tracker.yml` 已经设置为每天自动运行一次。

也可以手动运行：

`Actions → Druni Price Tracker → Run workflow`

## 5. 本地测试

```bash
pip install -r requirements.txt
python -m playwright install chromium
python main.py
```

## 注意

Druni 页面结构可能变化。如果抓不到价格，脚本会尝试多种方式：JSON-LD、meta price、页面可见价格文本。仍失败时，需要根据当时网页结构调整选择器。
