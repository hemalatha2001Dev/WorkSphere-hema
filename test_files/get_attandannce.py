from zk import ZK
from devices import DEVICES

for device in DEVICES:

    try:
        print(f"\nConnecting to {device['name']} ({device['ip']})")

        zk = ZK(device["ip"], port=4370, timeout=10, password=0)

        conn = zk.connect()

        print("✅ Connected to device!")

        print("\n--- USERS ---")
        users = conn.get_users()

        for u in users:
            print(
                f"{device['name']} | "
                f"ID: {u.user_id} | "
                f"Name: {u.name}"
            )

        print(f"✅ Total Users: {len(users)}")

        print("\n--- ATTENDANCE LOGS ---")

        attendance = conn.get_attendance()

        for log in attendance:
            print(
                f"{device['name']} | "
                f"User: {log.user_id} | "
                f"Time: {log.timestamp} | "
                f"Punch: {log.punch}"
            )

        conn.disconnect()

        print(f"✅ Finished {device['name']}")

    except Exception as e:
        print(f"❌ Error in {device['name']}: {e}")