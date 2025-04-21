from fastapi import APIRouter, Request
import httpx

# we need a cloudflare tunnel from 9000 and 3000
WAHA_URL = "https://springer-along-peers-stockholm.trycloudflare.com"
SESSION = "default"
# TODO: CHANGE THIS (and use env vars)
API_KEY = "admin"

HEADERS = {"api_key": API_KEY}

router = APIRouter()


@router.post("/webhook")
async def handle_whatsapp_message(request: Request):
    data = await request.json()
    print("🚀 ~ data:", data)

    # Only handle incoming messages
    if data.get("event") == "message":
        message = data["payload"]
        chat_id = message["from"]
        message_id = message["id"]
        print("🚀 ~ message:", message)
        print("🚀 ~ chat_id:", chat_id)
        print("🚀 ~ message_id:", message_id)


        # React with emoji
        async with httpx.AsyncClient() as client:
            res = await client.put(
                f"{WAHA_URL}/api/reaction",
                headers=HEADERS,
                json={"session": SESSION, "messageId": message_id, "reaction": "😊"},
            )

            print(res.json(), "here")



        # Send poll with "Confirm" and "Cancel"
        # await httpx.post(
        #     f"{WAHA_URL}/api/sendPoll",
        #     json={
        #         "session": SESSION,
        #         "chatId": chat_id,
        #         "name": "Do you confirm?",
        #         "options": ["Confirm", "Cancel"]
        #     },
        #     headers=HEADERS
        # )

    elif data.get("event") == "poll_vote":
        poll = data["payload"]
        print("🚀 ~ poll:", poll)
        chat_id = poll["chatId"]
        print("🚀 ~ chat_id:", chat_id)
        vote = poll["selectedOption"]
        print("🚀 ~ vote:", vote)

        # # Respond based on the user's choice
        # await httpx.post(
        #     f"{WAHA_URL}/api/sendText",
        #     json={"session": SESSION, "chatId": chat_id, "text": f"You chose: {vote}"},
        #     headers=HEADERS
        # )

    return {"status": "ok"}
