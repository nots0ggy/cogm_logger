import { clipboard, storage } from '@neutralinojs/lib';

export type Config = {
	patch: string;
	identifier: string;
	player_one: number;
	player_two: number;
	guild: number;
	kill: number;
	auto_scroll: boolean;
	include_characters: boolean;
	all_interfaces: boolean;
	ip_filter: boolean;
	live_output_path: string;
	record_pcap: boolean;
	cogm_url: string;
	cogm_token: string;
	cogm_guild: string;
	// Which captured column is the Killer / Victim / Guild, as column indices
	// (0-4). Persisted so the order set in one war (or in Settings) applies to
	// the next. Defaults to 0/1/2, which is the long-standing positional order.
	name_order: { killer: number; victim: number; guild: number };
};

export type LogType = {
	identifier: string;
	time: string;
	names: { name: string; offset: number }[];
	hex: string;
};

export type Log = {
	time: string;
	names: string[];
	kill: boolean;
}

export function get_date() {
	const today = new Date();
	const isoDate = today.toISOString().substr(0, 10);
	return isoDate;
}

export function get_formatted_date(date_string: string) {
	const date = new Date(date_string);
	const formatter = new Intl.DateTimeFormat('de', {
		day: '2-digit',
		month: '2-digit',
		year: 'numeric'
	});
	return formatter.format(date).replace(/\//g, '.');
}

export function stringify_config(config: Config) {
	return `[GENERAL]
patch=${config.patch ? get_formatted_date(config.patch) : get_formatted_date(get_date())}
[IP]
server_1 = 20.76.13
server_2 = 20.76.14
[PACKAGE]
identifier = ${config.identifier}
guild = ${config.guild}
player_one = ${config.player_one}
player_two = ${config.player_two}
kill = ${config.kill}
log_length = 600
name_length = 64`;
}

/* 
export async function get_config() {
	const config_parser = new ConfigIniParser.ConfigIniParser();
	const raw_config = await filesystem.readFile('config.ini');
	const parsed_config = config_parser.parse(raw_config);
	const config: Config = {
		identifier: parsed_config.get('PACKAGE', 'identifier'),
		player_one: parsed_config.get('PACKAGE', 'player_one'),
		player_two: parsed_config.get('PACKAGE', 'player_two'),
		guild: parsed_config.get('PACKAGE', 'guild'),
		kill: parsed_config.get('PACKAGE', 'kill')
	};
	return config;
}

export async function update_config(config: Config) {
	filesystem.writeFile('config.ini', stringify_config(config));
} */

export async function update_config(config: Config) {
	await storage.setData('config', JSON.stringify(config));
	return config;
}

export async function get_config(): Promise<Config> {
	const config = await storage.getData('config').catch((e) => console.error(e));
	if (config) {
		const parsed: Config = JSON.parse(config);
		if (parsed.include_characters === undefined) {
			parsed.include_characters = true;
		}
		if (parsed.record_pcap === undefined) {
			parsed.record_pcap = false;
		}
		if (parsed.cogm_url === undefined) {
			parsed.cogm_url = 'https://cogm.app';
		}
		if (parsed.cogm_token === undefined) {
			parsed.cogm_token = '';
		}
		if (parsed.cogm_guild === undefined) {
			parsed.cogm_guild = '';
		}
		if (
			parsed.name_order === undefined ||
			typeof parsed.name_order.killer !== 'number' ||
			typeof parsed.name_order.victim !== 'number' ||
			typeof parsed.name_order.guild !== 'number'
		) {
			parsed.name_order = { killer: 0, victim: 1, guild: 2 };
		}

		return parsed;
	} else {
		return {
			identifier: '',
			player_one: 0,
			player_two: 0,
			guild: 0,
			kill: 0,
			patch: '',
			auto_scroll: true,
			include_characters: true,
			all_interfaces: true,
			ip_filter: false,
			live_output_path: '',
			record_pcap: false,
			cogm_url: 'https://cogm.app',
			cogm_token: '',
			cogm_guild: '',
			name_order: { killer: 0, victim: 1, guild: 2 }
		};
	}
}

export function copy_to_clipboard(config: Config) {
	clipboard.writeText(stringify_config(config));
}

export const PERSONAL_FAMILY_NAME_KEY = 'personal_family_name';

// One representative kill's names, saved after a capture so the Settings page
// can show the name-order dropdowns without a live war. Names only; the order
// itself lives in config.name_order.
export type NameOrderSample = { names: { name: string; offset: number }[] };
const NAME_ORDER_SAMPLE_KEY = 'name_order_sample';

export async function save_name_order_sample(sample: NameOrderSample): Promise<void> {
	try {
		await storage.setData(NAME_ORDER_SAMPLE_KEY, JSON.stringify(sample));
	} catch {
		/* best-effort; a missing sample only means Settings asks for a capture */
	}
}

export async function get_name_order_sample(): Promise<NameOrderSample | null> {
	try {
		const raw = await storage.getData(NAME_ORDER_SAMPLE_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw);
		if (!parsed || !Array.isArray(parsed.names) || parsed.names.length === 0) return null;
		return parsed as NameOrderSample;
	} catch {
		return null;
	}
}

// The alliance roster pulled from CoGM (GET /api/logger/roster) after the
// token verifies. It anchors name-order auto-detection: own family names pin
// the Killer (my-side) column, enemy family names pin the Victim column, and
// the in-game guild names pin the Guild column. `configured` is false when the
// user hasn't set up an Own Alliance on CoGM, in which case auto-detect falls
// back to its offline heuristic. Stored verbatim from the endpoint; matching
// normalizes (lowercase, strip spaces) at compare time.
export type CogmRoster = {
	configured: boolean;
	guildName: string; // CoGM website guild name, for display only
	guilds: string[]; // own alliance in-game guild names
	enemyGuilds: string[]; // enemy alliances' in-game guild names
	ownFamilyNames: string[];
	enemyFamilyNames: string[];
	fetchedAt: string; // ISO timestamp of the fetch
};
const COGM_ROSTER_KEY = 'cogm_roster';

export async function save_cogm_roster(roster: CogmRoster): Promise<void> {
	try {
		await storage.setData(COGM_ROSTER_KEY, JSON.stringify(roster));
	} catch {
		/* best-effort; a missing roster only means auto-detect uses the fallback */
	}
}

export async function get_cogm_roster(): Promise<CogmRoster | null> {
	try {
		const raw = await storage.getData(COGM_ROSTER_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw);
		if (!parsed || typeof parsed !== 'object') return null;
		// Tolerate older/partial blobs: coerce every field to a safe default so a
		// stale shape can never crash the detection that reads it.
		return {
			configured: parsed.configured === true,
			guildName: typeof parsed.guildName === 'string' ? parsed.guildName : '',
			guilds: Array.isArray(parsed.guilds) ? parsed.guilds : [],
			enemyGuilds: Array.isArray(parsed.enemyGuilds) ? parsed.enemyGuilds : [],
			ownFamilyNames: Array.isArray(parsed.ownFamilyNames) ? parsed.ownFamilyNames : [],
			enemyFamilyNames: Array.isArray(parsed.enemyFamilyNames) ? parsed.enemyFamilyNames : [],
			fetchedAt: typeof parsed.fetchedAt === 'string' ? parsed.fetchedAt : ''
		};
	} catch {
		return null;
	}
}

export async function clear_cogm_roster(): Promise<void> {
	try {
		await storage.setData(COGM_ROSTER_KEY, '');
	} catch {
		/* best-effort */
	}
}

export function calculate_kd(kills: number, deaths: number): string {
	if (deaths === 0) {
		return kills > 0 ? kills.toFixed(2) : '0.00';
	}
	return (kills / deaths).toFixed(2);
}

export function hexToString(hex: string) {
	let string = '';
	for (let i = 0; i < hex.length; i += 2) {
		string += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
	}
	return string;
}
