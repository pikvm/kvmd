const globals = require("/usr/lib/node_modules/globals/index.js");

module.exports = [
	{
		languageOptions: {
			globals: globals.browser,
			parserOptions: {
				ecmaVersion: 2025,
				sourceType: "module",
				allowImportExportEverywhere: true,
				requireConfigFile: false,
			},
		},
		rules: {
			indent: [
				"error",
				"tab",
				{SwitchCase: 1},
			],
			"linebreak-style": [
				"error",
				"unix",
			],
			quotes: [
				"error",
				"double",
			],
			"quote-props": [
				"error",
				"always",
			],
			"semi": [
				"error",
				"always",
			],
			"comma-dangle": [
				"error",
				"always-multiline",
			],
			"no-unused-vars": [
				"error",
				{vars: "local", args: "after-used"},
			],
		},
	},

];
