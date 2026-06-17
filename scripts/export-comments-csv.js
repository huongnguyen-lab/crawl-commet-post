const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const input = path.join(root, 'data', 'raw', 'comments.json');
const output = path.join(root, 'data', 'processed', 'comments.csv');
const json = JSON.parse(fs.readFileSync(input, 'utf8'));
const rows = Array.isArray(json.data) ? json.data : [];
const preferredHeaders = ['id', 'message', 'created_time', 'like_count', 'comment_count'];
const discoveredHeaders = [...new Set(rows.flatMap((row) => Object.keys(row)))];
const headers = [
  ...preferredHeaders.filter((header) => discoveredHeaders.includes(header)),
  ...discoveredHeaders.filter((header) => !preferredHeaders.includes(header)),
];

function escapeCsv(value) {
  if (value === null || value === undefined) {
    return '';
  }

  const text = String(value).replace(/\r?\n/g, ' ');
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const csv = [
  headers.join(','),
  ...rows.map((row) => headers.map((header) => escapeCsv(row[header])).join(',')),
].join('\r\n');

fs.writeFileSync(output, `\uFEFF${csv}`, 'utf8');
console.log(`wrote ${output} with ${rows.length} rows and ${headers.length} columns`);
console.log(headers.join(','));
