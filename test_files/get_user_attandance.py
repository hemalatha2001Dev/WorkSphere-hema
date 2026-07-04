from zk import ZK
from devices import DEVICES

# Get user input from terminal
user_id = input("Enter the User ID: ").strip()

found = False

for device in DEVICES:

    try:
        print(f"\nConnecting to {device['name']} ({device['ip']})")

        zk = ZK(device["ip"], port=4370, timeout=10, password=0)

        conn = zk.connect()

        print("✅ Connected!")

        attendance = conn.get_attendance()

        for log in attendance:

            if str(log.user_id) == user_id:

                found = True

                print(
                    f"{device['name']} | "
                    f"User ID: {log.user_id} | "
                    f"Time: {log.timestamp} | "
                    f"Punch: {log.punch}"
                )

        conn.disconnect()

    except Exception as e:
        print(f"❌ Error in {device['name']}: {e}")

if not found:
    print(f"\nNo attendance records found for User ID {user_id}")
else:
    print("\n✅ Search Completed")