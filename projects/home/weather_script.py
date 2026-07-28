import argparse
import requests
from bs4 import BeautifulSoup
import tools

def get_weather(query):
    url = f"https://www.google.com/search?q={query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Find the weather information
    weather_info = soup.find('div', class_='BNeawe iBp4i AP7Wnd')
    
    if weather_info:
        return weather_info.text.strip()
    else:
        return "Weather information not found."

def main():
    parser = argparse.ArgumentParser(description="Get today's weather from Google and save it to a file using file_ops.")
    parser.add_argument("location", type=str, help="Your location")
    parser.add_argument("-o", "--output", type=str, default="weather.txt", help="Output file path (default: weather.txt)")
    args = parser.parse_args()

    weather = get_weather(args.location)
    
    # Save the weather data using the file_ops tool
    tools.file_ops.write(args.output, f"Weather for {args.location}: {weather}")
    
    print(f"Weather saved to {args.output}")

if __name__ == "__main__":
    main()