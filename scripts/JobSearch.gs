/**
 * Job Search Sheet Sync — Apps Script
 *
 * Deploy this as a Web App (Execute as: Me, Who has access: Anyone).
 * The Python update_sheet.py script POSTs job data to the deployed URL.
 *
 * Sheet columns (auto-created on first run):
 *   A: Rating  B: Title  C: Company  D: Location  E: Posted
 *   F: Source  G: Apply  H: First Seen
 *
 * Each sync:
 *   1. Removes rows where "First Seen" + age > 7 days
 *   2. Appends new jobs (deduplicated by Apply URL)
 *   3. Re-sorts by Rating desc, then First Seen desc
 */

var SHEET_NAME  = "Jobs";
var COL_RATING  = 1;
var COL_TITLE   = 2;
var COL_COMPANY = 3;
var COL_LOCATION= 4;
var COL_POSTED  = 5;
var COL_SOURCE  = 6;
var COL_URL     = 7;
var COL_SEEN    = 8;
var NCOLS       = 8;

// ---------------------------------------------------------------------------
// Entry point — called by HTTP POST from update_sheet.py
// ---------------------------------------------------------------------------
function doPost(e) {
  try {
    var payload = JSON.parse(e.postData.contents);
    var action  = payload.action || "sync";

    if (action === "sync") {
      var result = syncJobs(payload.jobs || []);
      return jsonResponse({ ok: true, added: result.added, pruned: result.pruned, total: result.total });
    }

    if (action === "clear") {
      clearAllJobs();
      return jsonResponse({ ok: true, action: "cleared" });
    }

    if (action === "boston_companies") {
      var result = syncBostonCompanies(payload.rows || []);
      return jsonResponse({ ok: true, total: result.total });
    }

    return jsonResponse({ ok: false, error: "Unknown action: " + action });
  } catch (err) {
    return jsonResponse({ ok: false, error: err.message });
  }
}

// ---------------------------------------------------------------------------
// GET handler — health check / quick stats
// ---------------------------------------------------------------------------
function doGet(e) {
  var sheet = getOrCreateSheet();
  var rows  = sheet.getLastRow() - 1;  // subtract header
  return jsonResponse({ ok: true, rows: Math.max(0, rows), sheet: SHEET_NAME });
}

// ---------------------------------------------------------------------------
// Core sync logic
// ---------------------------------------------------------------------------
function syncJobs(incomingJobs) {
  var sheet   = getOrCreateSheet();
  var today   = new Date();
  today.setHours(0, 0, 0, 0);

  // --- 1. Read existing rows and prune missing ones (Hard Sync) -----------
  var lastRow    = sheet.getLastRow();
  var existingUrls = {};
  var incomingUrls = {};
  var rowsToPrune  = [];

  for (var j = 0; j < incomingJobs.length; j++) {
    if (incomingJobs[j].url) {
      incomingUrls[incomingJobs[j].url] = true;
    }
  }

  if (lastRow > 1) {
    var data = sheet.getRange(2, 1, lastRow - 1, NCOLS).getValues();
    // Read formulas for the URL column so we can compare actual URLs (not display text "Apply")
    var urlFormulas = sheet.getRange(2, COL_URL, lastRow - 1, 1).getFormulas();
    // Instead of deleting rows one by one (which is very slow and causes timeouts),
    // let's build an array of rows to keep, clear the sheet, and put them back.
    var rowsToKeep = [];
    for (var i = 0; i < data.length; i++) {
      // Extract the actual URL from the HYPERLINK formula or raw value
      var rawUrl = urlFormulas[i] ? urlFormulas[i][0] : "";
      var url = "";
      if (rawUrl && rawUrl.indexOf('=HYPERLINK(') === 0) {
        // Parse =HYPERLINK("url","text") — extract first quoted string
        var match = rawUrl.match(/=HYPERLINK\("([^"]*)"/);
        if (match) url = match[1];
      } else {
        url = data[i][COL_URL - 1] || "";
      }
      // Keep if the URL is in today's incoming list (or if there is no URL, maybe it's a manual note)
      if (!url || incomingUrls[url]) {
        rowsToKeep.push(data[i]);
        if (url) existingUrls[url] = true;
      } else {
        rowsToPrune.push(i + 2); // Just for logging the count
      }
    }
    
    // Clear old data and write back the ones we are keeping
    if (rowsToPrune.length > 0) {
      sheet.getRange(2, 1, lastRow - 1, NCOLS).clearContent();
      if (rowsToKeep.length > 0) {
        sheet.getRange(2, 1, rowsToKeep.length, NCOLS).setValues(rowsToKeep);
        
        // Re-apply clickable links for the kept rows
        for (var n = 0; n < rowsToKeep.length; n++) {
          var link = rowsToKeep[n][COL_URL - 1];
          if (link) {
            sheet.getRange(n + 2, COL_URL).setFormula('=HYPERLINK("' + link + '","Apply")');
          }
        }
      }
    }
  }

  // --- 2. Append new jobs -------------------------------------------------
  var todayStr = Utilities.formatDate(today, Session.getScriptTimeZone(), "yyyy-MM-dd");
  var newRows  = [];

  for (var j = 0; j < incomingJobs.length; j++) {
    var job = incomingJobs[j];
    var url = job.url || "";
    if (!url || existingUrls[url]) continue;

    newRows.push([
      job.rating    || "",
      job.title     || "",
      job.company   || "",
      job.location  || "",
      job.date_posted || "",
      job.source    || "",
      url,
      todayStr,
    ]);
  }

  if (newRows.length > 0) {
    var startRow = sheet.getLastRow() + 1;
    sheet.getRange(startRow, 1, newRows.length, NCOLS).setValues(newRows);

    // Make Apply URLs clickable
    for (var n = 0; n < newRows.length; n++) {
      var cell = sheet.getRange(startRow + n, COL_URL);
      var link = newRows[n][COL_URL - 1];
      if (link) {
        cell.setFormula('=HYPERLINK("' + link + '","Apply")');
      }
    }
  }

  // --- 3. Sort: Rating desc (⭐⭐⭐ first), then First Seen desc -----------
  var finalLast = sheet.getLastRow();
  if (finalLast > 2) {
    sheet.getRange(2, 1, finalLast - 1, NCOLS).sort([
      { column: COL_RATING, ascending: false },
      { column: COL_SEEN,   ascending: false },
    ]);
  }

  // --- 4. Style header and freeze -----------------------------------------
  styleSheet(sheet);

  return {
    added:  newRows.length,
    pruned: rowsToPrune.length,
    total:  Math.max(0, sheet.getLastRow() - 1),
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getOrCreateSheet() {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);

  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    var headers = ["Rating", "Title", "Company", "Location", "Posted", "Source", "Apply", "First Seen"];
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    styleSheet(sheet);
  }

  return sheet;
}

function styleSheet(sheet) {
  // Header row
  var headerRange = sheet.getRange(1, 1, 1, NCOLS);
  headerRange.setBackground("#1a1a2e");
  headerRange.setFontColor("#ffffff");
  headerRange.setFontWeight("bold");

  // Freeze header
  sheet.setFrozenRows(1);

  // Column widths
  sheet.setColumnWidth(COL_RATING,   70);
  sheet.setColumnWidth(COL_TITLE,   260);
  sheet.setColumnWidth(COL_COMPANY, 160);
  sheet.setColumnWidth(COL_LOCATION,180);
  sheet.setColumnWidth(COL_POSTED,   90);
  sheet.setColumnWidth(COL_SOURCE,  120);
  sheet.setColumnWidth(COL_URL,      70);
  sheet.setColumnWidth(COL_SEEN,    100);

  // Colour-code rating rows
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    var ratings = sheet.getRange(2, COL_RATING, lastRow - 1, 1).getValues();
    for (var i = 0; i < ratings.length; i++) {
      var row    = sheet.getRange(i + 2, 1, 1, NCOLS);
      var rating = ratings[i][0];
      if      (rating === "⭐⭐⭐") row.setBackground("#dafbe1");
      else if (rating === "⭐⭐")  row.setBackground("#fff8c5");
      else                         row.setBackground("#f6f8fa");
    }
  }
}

function syncBostonCompanies(rows) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheetName = "Boston companies";
  var sheet = ss.getSheetByName(sheetName);

  if (!sheet) {
    sheet = ss.insertSheet(sheetName);
  }

  // Clear existing data
  if (sheet.getLastRow() > 1) {
    sheet.getRange(2, 1, sheet.getLastRow() - 1, 3).clearContent();
  }

  // Headers
  var headers = ["Company", "ATS", "Careers URL"];
  sheet.getRange(1, 1, 1, 3).setValues([headers]);

  // Data rows
  var dataRows = [];
  for (var i = 0; i < rows.length; i++) {
    dataRows.push([
      rows[i].company || "",
      rows[i].ats || "",
      rows[i].careers_url || ""
    ]);
  }

  if (dataRows.length > 0) {
    sheet.getRange(2, 1, dataRows.length, 3).setValues(dataRows);

    // Make URLs clickable
    for (var n = 0; n < dataRows.length; n++) {
      var url = dataRows[n][2];
      var name = dataRows[n][0];
      if (url) {
        sheet.getRange(n + 2, 3).setFormula('=HYPERLINK("' + url + '","' + name + ' Careers")');
      }
    }
  }

  // Style
  var headerRange = sheet.getRange(1, 1, 1, 3);
  headerRange.setBackground("#1a1a2e");
  headerRange.setFontColor("#ffffff");
  headerRange.setFontWeight("bold");
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 220);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 350);

  return { total: dataRows.length };
}

function clearAllJobs() {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME);
  if (sheet) ss.deleteSheet(sheet);
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
