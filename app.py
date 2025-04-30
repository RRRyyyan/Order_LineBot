from flask import Flask, request, abort

from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexBubble,
    FlexImage,
    FlexMessage,
    FlexBox,
    FlexText,
    FlexIcon,
    FlexButton,
    FlexSeparator,
    FlexContainer,
    URIAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)
import json

app = Flask(__name__)

configuration = Configuration(access_token='aQ68TgdOVZBKyynHpYEQEMRwWCzCLF+qLSi2qQGj01XSmJUjS9NWuW9qbWpj3cMnCG1lY8Z+tqOyzhsd7vIcuX5lJ7Gz5AwnNGdRP/4PZKG72OA1QNL8mMG8+kx9p2h1hQjZyznQIyaRQ1NLwS11iQdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('3aec6ec2962f94b5366332b3b1f38261')


@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        if text == 'flex':
            url = request.url_root + 'static/Logo.jpg'
            url = url.replace("http", "https")
            app.logger.info("url=" + url)
            bubble = FlexBubble(
                direction='ltr',
                hero=FlexImage(
                    url=url,
                    size='full',
                    aspect_ratio='20:13',
                    aspect_mode='cover',
                    action=URIAction(uri='https://www.facebook.com/NTUEBIGDATAEDU', label='label')
                ),
                body=FlexBox(
                    layout='vertical',
                    contents=[
                        # title
                        FlexText(text='教育大數據', weight='bold', size='xl'),
                        # review
                        FlexBox(
                            layout='baseline',
                            margin='md',
                            contents=[
                                FlexIcon(size='sm', url="https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"),
                                FlexIcon(size='sm', url="https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"),
                                FlexIcon(size='sm', url="https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"),
                                FlexIcon(size='sm', url="https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"),
                                FlexIcon(size='sm', url="https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"),
                                FlexText(text='5.0', size='sm', color='#999999', margin='md', flex=0)
                            ]
                        ),
                        # info
                        FlexBox(
                            layout='vertical',
                            margin='lg',
                            spacing='sm',
                            contents=[
                                FlexBox(
                                    layout='baseline',
                                    spacing='sm',
                                    contents=[
                                        FlexText(
                                            text='Place',
                                            color='#aaaaaa',
                                            size='sm',
                                            flex=1
                                        ),
                                        FlexText(
                                            text='Da\'an District, Taipei ',
                                            wrap=True,
                                            color='#666666',
                                            size='sm',
                                            flex=5
                                        )
                                    ],
                                ),
                                FlexBox(
                                    layout='baseline',
                                    spacing='sm',
                                    contents=[
                                        FlexText(
                                            text='Time',
                                            color='#aaaaaa',
                                            size='sm',
                                            flex=1
                                        ),
                                        FlexText(
                                            text="10:00 - 23:00",
                                            wrap=True,
                                            color='#666666',
                                            size='sm',
                                            flex=5,
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ],
                ),
                footer=FlexBox(
                    layout='vertical',
                    spacing='sm',
                    contents=[
                        # callAction
                        FlexButton(
                            style='link',
                            height='sm',
                            action=URIAction(label='CALL', uri='tel:0911880932'),
                        ),
                        # separator
                        FlexSeparator(),
                        # websiteAction
                        FlexButton(
                            style='link',
                            height='sm',
                            action=URIAction(label='WEBSITE', uri='https://www.facebook.com/NTUEBIGDATAEDU')
                        )
                    ]
                ),
            )
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[FlexMessage(alt_text="hello", contents=bubble)]
                )
            )

        elif text == 'flex message':
            line_flex_json = {
                "type": "bubble",
                "hero": {
                    "type": "image",
                    "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png",
                    "size": "full",
                    "aspectRatio": "20:13",
                    "aspectMode": "cover",
                    "action": {
                    "type": "uri",
                    "uri": "http://linecorp.com/"
                    }
                },
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                    {
                        "type": "text",
                        "text": "Brown Cafe",
                        "weight": "bold",
                        "size": "xl"
                    },
                    {
                        "type": "box",
                        "layout": "baseline",
                        "margin": "md",
                        "contents": [
                        {
                            "type": "icon",
                            "size": "sm",
                            "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"
                        },
                        {
                            "type": "icon",
                            "size": "sm",
                            "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"
                        },
                        {
                            "type": "icon",
                            "size": "sm",
                            "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"
                        },
                        {
                            "type": "icon",
                            "size": "sm",
                            "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png"
                        },
                        {
                            "type": "icon",
                            "size": "sm",
                            "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gray_star_28.png"
                        },
                        {
                            "type": "text",
                            "text": "4.0",
                            "size": "sm",
                            "color": "#999999",
                            "margin": "md",
                            "flex": 0
                        }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "spacing": "sm",
                        "contents": [
                        {
                            "type": "box",
                            "layout": "baseline",
                            "spacing": "sm",
                            "contents": [
                            {
                                "type": "text",
                                "text": "Place",
                                "color": "#aaaaaa",
                                "size": "sm",
                                "flex": 1
                            },
                            {
                                "type": "text",
                                "text": "Miraina Tower, 4-1-6 Shinjuku, Tokyo",
                                "wrap": True,
                                "color": "#666666",
                                "size": "sm",
                                "flex": 5
                            }
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "baseline",
                            "spacing": "sm",
                            "contents": [
                            {
                                "type": "text",
                                "text": "Time",
                                "color": "#aaaaaa",
                                "size": "sm",
                                "flex": 1
                            },
                            {
                                "type": "text",
                                "text": "10:00 - 23:00",
                                "wrap": True,
                                "color": "#666666",
                                "size": "sm",
                                "flex": 5
                            }
                            ]
                        }
                        ]
                    }
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                        "type": "uri",
                        "label": "CALL",
                        "uri": "https://www.facebook.com/NTUEBIGDATAEDU"
                        }
                    },
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                        "type": "uri",
                        "label": "WEBSITE",
                        "uri": "https://www.facebook.com/NTUEBIGDATAEDU"
                        }
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [],
                        "margin": "sm"
                    }
                    ],
                    "flex": 0
                }
            }
            line_flex_str = json.dumps(line_flex_json)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[FlexMessage(alt_text='詳細說明', contents=FlexContainer.from_json(line_flex_str))]
                )
            )

        else:
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=event.message.text)]
                )
            )