import re
import asyncio
import socket
import unicodedata
import urllib.parse

import aiohttp
import requests
from irctokens import build, Line
from ircrobots import Bot as BaseBot, Capability
from ircrobots import Server as BaseServer
from ircrobots import ConnectionParams

SERVERS = [
    ("piss", "irc.shitposting.space"),
]

wikilink_re = re.compile(r'\[\[(?:[^|\]]*\|)?([^]]+)]]')


class Server(BaseServer):
    def __init__(self, *args, **kwargs):
        super(Server, self).__init__(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.create_task(self.udp_stuff())

    async def line_read(self, line: Line):
        print(f"{self.name} < {line.format()}")
        if line.command == "PRIVMSG":
            await self.on_message(line)
        elif line.command == "001":
            await self.send(build("JOIN", ["#opers,#pissnet,#pisswiki"]))

    async def udp_stuff(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setblocking(True)
        s.settimeout(0)
        s.bind(("127.0.0.1", 1234))
        while True:
            try:
                (data, addr) = s.recvfrom(128 * 1024)
            except BlockingIOError:
                await asyncio.sleep(0.3)
                continue
            await self.send(build("PRIVMSG", ["#pisswiki", data.decode()]))

    async def on_message(self, line: Line):
        if line.hostmask.nickname == self.nickname:
            return
        # Check if it's a [[Wiki]] link
        message = line.params[-1].strip()
        if match := wikilink_re.findall(message):
            for i in match[0:3]:
                await self.on_wikilink(line, i)
        if message.startswith("!"):
            message = message.replace("!", '')
            command = message.split(" ")[0]
            params = message.split(" ")[1:]
            if command in ("server", "s") and len(params) > 0:
                await self.print_server_info(line, params[0])
            elif command in ("splitservers", "lost", "netsplit", "missing"):
                await self.split_servers(line)
            elif command in ("nospki", "nospkifp"):
                await self.no_spki(line)
            elif command == "u":
                await self.unicoder(line, params)
            elif command == "help":
                await self.msg(line, "Commands: Unicode stuff: !u <some char> | Server info: !server <servername> |"
                                     "Lost servers: !missing | Servers w/ no spki: !nospki")

    async def msg(self, line, msg):
        source = line.params[0]
        # If somebody sends a message to @#channel I will cry.
        if "#" not in line.params[0]:
            source = line.hostmask.nickname
        await self.send(build("PRIVMSG", [source, msg]))

    async def _semantic_query(self, query):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://wiki.letspiss.net/api.php',
                                   params={'action': 'ask', 'query': query, 'format': 'json'}) as resp:
                result = await resp.json()
                return result['query']['results']

    async def _shitposting_query(self):
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.shitposting.space/servers.json") as resp:
                return await resp.json()

    async def unicoder(self, line: Line, params: list):
        chars = params[0]
        if len(chars) > 10:
            return await self.msg(line, "Sorry, your input is too long! The maximum is 10 bytes")

        reply = ""
        for char in chars:
            try:
                name = unicodedata.name(char).replace('-{0:04X}'.format(ord(char)), '')
            except ValueError:
                name = "No name found"
            reply += "U+{0:04X} {1} ({2}) ".format(ord(char), name, char)

        return await self.msg(line, reply)

    async def no_spki(self, line: Line):
        query = "[[Server:+]] [[Category:Nodes without SPKIFP]] [[Node Type::Leaf]] [[Node Status::Active]]|limit=500"
        wikinodes = await self._semantic_query(query)
        wikinodes = [x.replace('Server:', '').lower() for x in wikinodes.keys()]

        source = line.params[0]
        if "#" not in line.params[0]:
            source = line.hostmask.nickname
        wikinodes = ", ".join(wikinodes)
        await self.send(build("PRIVMSG", [source, f"Nodes without spkifp: {wikinodes}"]))

    async def split_servers(self, line: Line):
        query = "[[Server:+]] [[Category:Nodes]] [[Node Status::Active]]|?Server Name|?IPv4|?IPv6|limit=500"
        wikinodes = await self._semantic_query(query)
        wikinodes = [x.replace('Server:', '').lower() for x in wikinodes.keys()]

        alldata = await self._shitposting_query()
        linkednodes = [x['name'].lower() for x in alldata['servers'].values()]
        linkednodes2 = [x['name'].lower() for x in alldata['servers'].values() if x['description'][0] != '~']

        splitnodes = list(set(wikinodes) - set(linkednodes))
        missingnodes = list(set(linkednodes2) - set(wikinodes))
        splitnodes.remove("pbody.polsaker.com")  # We don't wanna show that ugly fucker don't we

        splitnodes = ", ".join(splitnodes)
        missingnodes = ", ".join(missingnodes)
        await self.msg(line, f"Nodes currently marked as active but not linked to the network: {splitnodes}")
        if missingnodes:
            await self.msg(line, f"Nodes currently linked but missing in the wiki: {missingnodes}")

    async def get_server_info(self, servername):
        if len(servername) == 3 and "." not in servername:
            servername = servername.upper()
            query = f"[[Server:+]] [[Category:Nodes]] [[SID::{servername}]]|?Server Name|?Owner|?SPKIFP|?Location" \
                    "|?Node Type|?Node Status|?SID"
        else:
            query = f"[[Server:{servername}]]|?Server Name|?Owner|?SPKIFP|?Location" \
                    "|?Node Type|?Node Status|?SID"

        server = await self._semantic_query(query)
        alldata = await self._shitposting_query()

        if not server:
            return False
        server = list(server.values())[0]['printouts']
        data = {
            'servername': server['Server Name'][0] if server['Server Name'] else None,
            'owner': server['Owner'][0] if server['Owner'] else None,
            'spki': server['SPKIFP'][0] if server['SPKIFP'] else None,
            'location': server['Location'][0] if server['Location'] else None,
            'type': server['Node Type'][0] if server['Node Type'] else None,
            'status': server['Node Status'][0] if server['Node Status'] else None,
            'sid': server['SID'][0] if server['SID'] else None,
            'sdata': {},
        }
        links = [x for x in alldata['links'] if x[0] == data['sid'] or x[1] == data['sid']]
        data['links'] = len(links)
        data['sdata'] = alldata['servers'].get(data['sid'].upper(), {})
        return data

    def is_deprecated(self, version: str) -> bool:
        if not version:
            return False

        if version.startswith("UnrealIRCd-5.0"):
            return True
        if version.startswith("UnrealIRCd-5.2.0"):
            return True
        return False

    async def print_server_info(self, line: Line, servername):
        source = line.params[0]
        if "#" not in line.params[0]:
            source = line.hostmask.nickname
        data = await self.get_server_info(servername)
        if not data:
            pagedata = await self.get_pagedata(servername)
            title = pagedata['title'].replace('Server:', '')
            if pagedata['title'].startswith('Server:') and title != servername:
                return await self.print_server_info(line, title)
            return await self.send(build("PRIVMSG", [source, f"Error: Server {servername} not found in the wiki?"]))
        message = ""
        if data['status'] == "Active":
            if self.is_deprecated(data['sdata'].get('version')):
                message += f"[\00308{data['servername']}\003 \002(Running outdated unreal)\002] "
            elif data['links'] == 0:
                message += f"[\00308{data['servername']}\003 \002(Active but not linked)\002] "
            else:
                message += f"[\00303{data['servername']}\003] "
        else:
            message += f"[\00304{data['servername']}\003 ({data['status']})] "

        owner = re.sub(r"\[\[.*?\|(.+?)]]", "\\1", data['owner'], 0, re.MULTILINE)
        owner = owner.split(" ")
        owner = " ".join([x[0] + "\u200b" + x[1:] for x in owner])
        message += f"Type: \002{data['type']}\002, SID: {data['sid']}, location: {data['location']}, contact: {owner}"
        message += f", peers: {data['links']}"
        message += f" - https://wiki.letspiss.net/wiki/Server:{data['servername']}"
        await self.send(build("PRIVMSG", [source, message]))

    async def get_pagedata(self, page):
        async with aiohttp.ClientSession() as session:
            async with session.get('https://wiki.letspiss.net/api.php',
                                   params={
                                       'action': 'query', 'titles': page, 'format': 'json', 'redirects': ''
                                   }) as resp:
                data = await resp.json()
                return list(data['query']['pages'].values())[0]

    async def on_wikilink(self, line, page):
        if page.lower().startswith('server:'):
            await self.print_server_info(line, page.replace("Server:", ''))
            return
        source = line.params[0]
        if "#" not in line.params[0]:
            source = line.hostmask.nickname

        pagedata = await self.get_pagedata(page)
        if pagedata.get('missing', 'f') == '':
            await self.send(build("PRIVMSG", [source, f"Page '{page}' not found."]))
            return
        urititle = urllib.parse.quote_plus(pagedata['title'].replace(" ", "_")).replace("%3A", ":")
        urititle = urititle.replace("%2F", "/")
        if pagedata['title'].startswith("Server:"):
            await self.print_server_info(line, pagedata['title'].replace("Server:", ''))
            return
        await self.send(build("PRIVMSG", [source, f"[\002{pagedata['title']}\002] "
                                                  f"https://wiki.letspiss.net/wiki/{urititle}"]))


class Bot(BaseBot):
    def create_server(self, name: str):
        return Server(self, name)


async def main():
    bot = Bot()
    for name, host in SERVERS:
        params = ConnectionParams(f"Pisswiki", host, 6697, True)
        await bot.add_server(name, params)

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
