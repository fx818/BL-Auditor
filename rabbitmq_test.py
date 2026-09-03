import json
import os
import pika

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://astbuyiil298:kMCyDK4f@35.244.27.79:5672/astbuy")
print(f"Using RabbitMQ URL: {RABBITMQ_URL}")
RABBITMQ_QUEUE = os.getenv("RABBITMQ_QUEUE", "BL_AUDITOR")

OFR_IDS = [
    "149481683952",
    "149470181275",
    "149469795734",
    "149477344975",
    "149465878067",
    "149469767463",
    "149477909346",
    "149465747285",
    "149466222085",
    "149466224134",
    "149469936375",
    "149466231334",
    "149469955467",
    "149466233067",
    "149469986767",
    "149469942052",
    "149469934846",
    "149469920775",
    "149469939652",
    "149469963752",
    "149465912585",
    "149465912763",
    "149465751675",
    "149469934967",
    "149469920334",
    "149466351775",
    "149465920152",
    "149470000167",
    "149466307763",
    "149470155113",
    "149465920875",
    "149465925595",
    "149465657852",
    "149465755213",
    "149475629013",
    "149469919863",
    "149465752959",
    "149466323395",
]


def publish_offer_ids(ofr_ids):
    connection = pika.BlockingConnection(
        pika.URLParameters(RABBITMQ_URL)
    )
    channel = connection.channel()

    try:
        for ofr_id in ofr_ids:
            payload = {
                "args": {
                    "ofr_id": str(ofr_id),
                    "typ": 0
                }
            }

            message = json.dumps(payload, separators=(",", ":"))

            channel.basic_publish(
                exchange="",
                routing_key=RABBITMQ_QUEUE,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=1
                )
            )

            print(f"Published: {ofr_id}")

    finally:
        connection.close()

    print(f"\nDone. Published {len(ofr_ids)} messages.")


if __name__ == "__main__":
    publish_offer_ids(OFR_IDS)