"""
Utility to detect local and public IP addresses for server hosting
"""
import socket
import sys
import urllib.request
import json


def get_public_ip():
    """
    Detect the public IP address of the machine.
    This is the IP that external users will use to access your server.
    Uses multiple services as fallback for reliability.
    """
    services = [
        ('https://api.ipify.org?format=json', 'ip'),
        ('https://api.my-ip.io/ip.json', 'ip'),
        ('https://ipapi.co/json/', 'ip'),
        ('https://ifconfig.me/ip', None),  # Plain text response
    ]
    
    for service_url, json_key in services:
        try:
            with urllib.request.urlopen(service_url, timeout=5) as response:
                if json_key:
                    # JSON response
                    data = json.loads(response.read().decode())
                    return data[json_key]
                else:
                    # Plain text response
                    return response.read().decode().strip()
        except Exception as e:
            continue
    
    # If all services fail
    print("⚠️  Warning: Could not detect public IP. Using 0.0.0.0 (all interfaces)")
    return '0.0.0.0'


def get_local_ip():
    """
    Detect the local/private IP address of the machine.
    This is useful for internal network communication.
    """
    try:
        # Create a socket connection to Google's DNS (doesn't actually connect)
        # This helps us find the network interface that would be used for external connections
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        # Connect to Google's DNS server (8.8.8.8) on port 80
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        print(f"Error detecting local IP via external connection: {e}")
        
        # Fallback: Try to get hostname-based IP
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            if local_ip.startswith('127.'):
                # If we got localhost, try to get all IPs and pick the first non-localhost
                all_ips = socket.gethostbyname_ex(hostname)[2]
                for ip in all_ips:
                    if not ip.startswith('127.'):
                        return ip
            return local_ip
        except Exception as e2:
            print(f"Error detecting local IP via hostname: {e2}")
            # Last resort: return localhost
            return '127.0.0.1'


def get_all_local_ips():
    """
    Get all local IP addresses available on the machine.
    Useful for debugging or when you want to see all network interfaces.
    """
    hostname = socket.gethostname()
    try:
        # Get all IP addresses associated with the hostname
        ip_list = socket.gethostbyname_ex(hostname)[2]
        return [ip for ip in ip_list if not ip.startswith('127.')]
    except Exception as e:
        print(f"Error getting all IPs: {e}")
        return []


if __name__ == "__main__":
    print("=" * 60)
    print("🌐 Network IP Detection")
    print("=" * 60)
    print(f"\n📍 Hostname: {socket.gethostname()}")
    
    print(f"\n🌍 Public IP (External Access):")
    public_ip = get_public_ip()
    print(f"   {public_ip}")
    print(f"   Use this for: http://{public_ip}:8000")
    
    print(f"\n🏠 Local IP (Internal Network):")
    local_ip = get_local_ip()
    print(f"   {local_ip}")
    
    all_ips = get_all_local_ips()
    if all_ips:
        print(f"\n📡 All available local IPs:")
        for ip in all_ips:
            print(f"   - {ip}")
    
    print("\n" + "=" * 60)
    print("💡 For data center deployment, use the Public IP")
    print("=" * 60)
