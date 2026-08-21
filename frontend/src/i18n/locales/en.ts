import type { Messages } from '../types'

/**
 * English. Typed as `Messages`, so a key present in zh-TW and missing here is a
 * compile error rather than a blank label at runtime.
 *
 * Taiwan market vocabulary is kept in the terms an English-reading trader would
 * use: 張 is a "lot" (1,000 shares), 上市/上櫃 are the exchanges TWSE/TPEx, and
 * 乖離 is the bias against the moving average.
 */
export const en: Messages = {
  // ---------- shell ----------
  'app.title': 'ai-stockboard · Taiwan Stock Board',
  'app.brandSuffix': '-stockboard',
  'nav.market': 'Market',
  'nav.stock': 'Stocks',
  'nav.realtime': 'Realtime',
  'nav.realtimeLocked': 'sign in',
  'nav.realtimeLockedTitle': 'Realtime quotes need an account',
  'nav.notFound': 'This page does not exist',
  'health.connected': 'DB connected',
  'health.disconnected': 'DB unreachable',
  'lang.zh': '中',
  'lang.en': 'EN',
  'lang.zhTitle': '切換為繁體中文',
  'lang.enTitle': 'Switch to English',

  // ---------- user menu ----------
  'menu.login': 'Sign in',
  'menu.register': 'Sign up',
  'menu.guest': 'Menu',
  'menu.language': 'Language',
  'menu.admin': 'Admin',
  'menu.changePassword': 'Change password',
  'menu.logout': 'Sign out',

  // ---------- admin dashboard ----------
  'admin.title': 'Admin',
  'admin.lede': 'Every administration area, and how the system is doing.',
  'admin.back': '← Admin',
  'admin.system': 'System status',
  'admin.database': 'Database',
  'admin.scheduler': 'Scheduler',
  'admin.schedulerOn': 'Running ({timezone})',
  'admin.schedulerOff': 'Disabled -- jobs can only be run by hand',
  'admin.usersTitle': 'Users',
  'admin.usersDesc': 'Roles, deactivation, password resets.',
  'admin.usersPending': '{count} waiting for approval',
  'admin.usersStat': '{count} accounts',
  'admin.jobsTitle': 'Jobs',
  'admin.jobsDesc': 'Schedules, run history, manual runs.',
  'admin.jobsStat': '{count} jobs',
  'admin.jobsAttention': '{count} need attention',
  'admin.jobsHealthy': 'All healthy',
  'admin.codesTitle': 'Listing sync',
  'admin.listingCount': '{count} listed',
  'admin.listingSynced': 'Last synced {when}',
  'admin.listingNeverSynced': 'Never synced -- using the bundled snapshot',

  // ---------- login ----------
  'login.title': 'Sign in',
  'login.identifier': 'Username or email',
  'login.password': 'Password',
  'login.failed': 'Sign-in failed: {message}',
  'login.submitting': 'Signing in…',
  'login.submit': 'Sign in',
  'login.noAccount': 'No account yet?',
  'login.registerLink': 'Create one',
  'login.pendingApproval':
    'Your account has been created and is waiting for an administrator to approve it. These credentials will work once it is.',
  'login.locked': 'Too many attempts -- this account is temporarily locked. Try again shortly.',
  'login.lockedIn':
    'Too many attempts -- this account is temporarily locked. Try again in {minutes} minutes.',
  'login.throttled': 'Too many sign-in attempts. Try again shortly.',
  'login.throttledIn': 'Too many sign-in attempts. Try again in {minutes} minutes.',

  // ---------- register ----------
  'register.title': 'Sign up',
  'register.username': 'Username',
  'register.usernameHint': '3–32 characters, case-insensitive',
  'register.email': 'Email',
  'register.phone': 'Phone (optional)',
  'register.password': 'Password',
  'register.passwordHint': 'At least {min} characters',
  'register.confirm': 'Confirm password',
  'register.failed': 'Sign-up failed: {message}',
  'register.submitting': 'Signing up…',
  'register.submit': 'Sign up',
  'register.haveAccount': 'Already have an account?',
  'register.loginLink': 'Sign in',
  'register.approvalNotice':
    'New accounts on this site are reviewed by an administrator, so signing up will not sign you in.',
  'register.pendingTitle': 'Request submitted',
  'register.pendingBody':
    'The account {username} has been created and is waiting for an administrator to approve it. You can sign in and use realtime quotes once it is approved.',
  'register.pendingNote':
    'There is no email notification -- check with your administrator for the outcome.',
  'register.pendingHome': 'Back to the market',

  // ---------- change password ----------
  'changePassword.title': 'Change password',
  'changePassword.titleForced': 'Set a new password',
  'changePassword.forcedNotice':
    'An administrator reset your password. Nothing else in the app works until you choose one of your own.',
  'changePassword.current': 'Current password',
  'changePassword.currentForced': 'Temporary password from the administrator',
  'changePassword.next': 'New password',
  'changePassword.confirm': 'Confirm new password',
  'changePassword.failed': 'Change failed: {message}',
  'changePassword.submitting': 'Saving…',
  'changePassword.submit': 'Set new password',
  'changePassword.note':
    'Sessions on your other devices are signed out; this one stays signed in.',

  // ---------- password rules (utils/password.ts) ----------
  'password.tooShort': 'The password must be at least {min} characters',
  'password.tooLong':
    'The password may not exceed {max} bytes (about 24 Chinese characters)',
  'password.mismatch': 'The two passwords do not match',

  // ---------- sign-in invitation ----------
  'signIn.badge': 'Sign-in required',
  'signIn.registerNew': 'Create an account',
  'signIn.marketTitle': 'Sign in for the live index',
  'signIn.stockTitle': 'Sign in for live quotes',
  'signIn.realtimeTitle': 'Realtime quotes need an account',
  'signIn.realtimeLead':
    'The watchlist board pulls the latest trade price, change and five levels of bid/ask from the exchange every {seconds} seconds. Every quote costs an upstream request against a site-wide budget, which is why this page is kept for account holders.',
  'signIn.realtimeBenefit1':
    'Intraday quotes for up to {max} watchlist stocks, refreshed every {seconds} seconds',
  'signIn.realtimeBenefit2': 'Five levels of bid and ask, plus last-trade and total volume',
  'signIn.realtimeBenefit4': 'Best Four Point (Buy / Sell / Don\'t touch) on every card',
  'signIn.realtimeBenefit3':
    'The watchlist lives on your account — same list on any device or browser',
  'signIn.realtimeNote':
    'Historical candles, moving averages and Best Four Point for the index and individual stocks need no account and stay open to everyone.',
  'signIn.marketLockedNote':
    'The figures above are the last trading day’s close. Sign in for the intraday index level, refreshed every {seconds} seconds.',
  'signIn.stockLockedNote':
    'The figures above are the last trading day’s close. Sign in for intraday quotes and five levels of bid/ask.',

  // ---------- stock search ----------
  'search.placeholder': 'Stock code or name, e.g. 2330 or 台積電',
  'search.placeholderStock': 'Look up a stock, e.g. 2330',
  'search.placeholderWatchlist': 'Add to watchlist, e.g. 2330',
  'search.searching': 'Searching…',
  'search.noResults': 'No stock matches “{query}”',
  'search.truncated': '{total} matches, showing the first {shown}',
  'search.delisted': 'Delisted',

  // ---------- shared board chrome ----------
  'range.1m': '1M',
  'range.3m': '3M',
  'range.6m': '6M',
  'range.12m': '1Y',
  'badge.marketOpen': 'Open',
  'badge.marketClosed': 'Closed',
  'live.on': 'Auto-refresh on (every {seconds}s)',
  'live.off': 'Paused',
  'common.noData': 'No data',
  'error.loadFailed': 'Load failed: {message}',
  'error.dbHint':
    'If the message mentions the database, start PostgreSQL first: run docker compose up -d in the deployment/ directory.',

  // ---------- market dashboard / stock detail ----------
  'market.index': 'TAIEX',
  'market.indexSubtitle': 'Taiwan Capitalization Weighted Stock Index · TAIEX',
  'market.offHoursNote':
    'Outside trading hours (09:00–13:30 Taipei time) the last trading day is shown; turnover and volume are that day’s settled figures.',
  'stat.open': 'Open',
  'stat.high': 'High',
  'stat.low': 'Low',
  'stat.turnover': 'Turnover',
  'stat.volumeLots': 'Volume (lots)',
  'stat.quoteTime': 'Quote time',
  'stat.lastClose': 'Last close',
  'stat.lastDate': 'Last date',
  'stat.tradingDays': 'Trading days',
  'chart.candle': 'Candles',
  'chart.line': 'Close line',
  'chart.firstFetchNote':
    'The first lookup fetches month by month from TWSE, rate limited to 3 requests per 5 seconds. This takes a moment…',
  'history.fetchNote':
    'Fetched {fetched} month(s) from the source{detail}; {cached} month(s) served from the PostgreSQL cache',
  'history.fetchDetail': ' ({months})',
  'section.traditional': 'Rule-based analysis',
  'table.last10': 'Last 10 days',
  'table.date': 'Date',
  'table.close': 'Close',
  'table.closeIndex': 'Close',
  'table.change': 'Change',
  'table.turnover': 'Turnover',

  // ---------- opening intel ----------
  'open.title': 'Opening intel',
  'open.date': 'Date',
  'open.today': 'Today',
  'open.badgeHistory': 'History',
  'open.gap': 'Gap',
  'open.fromOpen': 'Since open',
  'open.colStock': 'Stock',
  'open.colLast': 'Last / close',
  'open.pattern.up.up': 'Gapped up, kept climbing',
  'open.pattern.up.down': 'Gapped up, faded',
  'open.pattern.up.flat': 'Gapped up, went nowhere',
  'open.pattern.down.up': 'Gapped down, recovered',
  'open.pattern.down.down': 'Gapped down, kept falling',
  'open.pattern.down.flat': 'Gapped down, went nowhere',
  'open.pattern.flat.up': 'Flat open, climbed',
  'open.pattern.flat.down': 'Flat open, fell',
  'open.pattern.flat.flat': 'Flat open, flat session',
  'open.watchlistTitle': 'Watchlist at the open',
  'open.watchlistEmpty': 'Your watchlist is empty. Add stocks with the search box above.',
  'open.watchlistSignInNote':
    'Sign in to follow your watchlist from the open while the session runs. Signed out, only settled trading days are shown.',
  'open.noSession': 'No session on {date} -- a holiday or a non-trading day.',
  'open.jumpLatest': 'Go to the latest trading day, {date}',
  'open.pendingReport':
    'TWSE has not published today’s daily report yet; the figures above come from the realtime quote.',
  'open.pendingLocked':
    'TWSE has not published today’s daily report yet. Sign in to follow the session from the open.',
  'open.noBars': 'No daily bars cached for this stock yet -- open its page to fetch them',
  'open.rowNoBars': 'No daily bars cached',
  'open.rowNoSession': 'No session',
  'open.futureDate': 'That day has not happened yet.',
  'open.showingLastSession':
    'Nothing for today yet; the figures below are the last trading day, {date}.',

  // ---------- realtime board ----------
  'realtime.refreshNow': 'Refresh now',
  'realtime.lastUpdated': 'Updated {time}',
  'realtime.watchlistSaveFailed': 'Could not save the watchlist: {message}',
  'realtime.watchlistFull':
    'The watchlist is full at {max} stocks. Remove a few before adding more.',
  'realtime.quoteError': '{code}: {message}',
  'realtime.sessionNote':
    'Realtime quotes are only published during the Taiwan session (Mon–Fri 09:00–13:30).',
  'realtime.empty': 'Your watchlist is empty. Use the search box above to add a stock.',
  'realtime.remove': 'Remove',
  'realtime.loading': 'Loading…',
  'realtime.noQuote': 'No quote yet',

  // ---------- realtime card ----------
  'quote.prevClose': 'Prev close',
  'quote.totalVolume': 'Total volume (lots)',
  'quote.tradeVolume': 'Last trade (lots)',
  'quote.bidDepth': 'Bid · 5 levels',
  'quote.askDepth': 'Ask · 5 levels',
  'quote.quotedAt': 'Quoted at {time}',

  // ---------- best four point ----------
  'bfp.title': 'Best Four Point',
  'bfp.loading': 'Scoring Best Four Point…',
  'bfp.hintBuy': 'Buy conditions met',
  'bfp.hintSell': 'Sell conditions met',
  'bfp.hintHold': 'No clear signal right now',
  'bfp.hintHoldThreshold': 'Neither the buy nor the sell threshold was reached',
  'bfp.asOf': 'data through {date}',
  'bfp.sampleSize': '{count} trading days',
  'bfp.ruleGrs': 'Corrected',
  'bfp.ruleGrsTitle':
    'Reference behaviour of Best Four Point: the bias-pivot pre-condition applies, and “volume down, price held/fell” compares against the previous close',
  'bfp.ruleTwstock': 'twstock',
  'bfp.ruleTwstockTitle':
    'twstock 1.5.1 as shipped: the bias pre-condition never fires, and “volume down, price held/fell” wrongly compares against the previous open',
  'bfp.twstockWarning':
    'Porting defects in twstock 1.5.1 disable the bias-pivot gate, so signals skew bullish. Shown for comparison only.',

  // ---------- moving averages ----------
  'ma.title': 'Moving averages',
  'ma.period': 'Period',
  'ma.average': 'Average',
  'ma.bias': 'Bias',
  'ma.ma20': 'MA20 (monthly)',
  'ma.ma60': 'MA60 (quarterly)',
  'ma.note': 'Bias = latest close − average',

  // ---------- dividends ----------
  'dividend.title': 'Dividends',
  'dividend.ttmCash': 'Cash dividend (TTM)',
  'dividend.yield': 'Yield',
  'dividend.emptyRecent': 'No recent or announced ex-dividend date for this stock.',
  'dividend.emptyHistory': 'No ex-dividend record in the period queried.',
  'dividend.colExDate': 'Ex-date',
  'dividend.colKind': 'Kind',
  'dividend.colCash': 'Cash dividend',
  'dividend.upcoming': 'announced',
  'dividend.noteRecent':
    'TPEx publishes only recent results and upcoming announcements — there is no annual history table of the kind TWSE provides.',
  'dividend.noteHistory':
    'Yield = trailing-12-month cash dividend ÷ latest close. Cash on ex-dividend rows is taken from the combined rights + dividend value.',
  'dividendKind.cash': 'Ex-dividend',
  'dividendKind.stock': 'Ex-rights',
  'dividendKind.both': 'Ex-rights & dividend',

  // ---------- charts ----------
  'chart.tooltipOpen': 'O',
  'chart.tooltipHigh': 'H',
  'chart.tooltipLow': 'L',
  'chart.tooltipClose': 'C',
  'chart.tooltipVolumeLots': 'Vol (lots)',
  'chart.legendCandle': 'Candles',
  'chart.legendClose': 'Close',
  'chart.volume': 'Volume',
  'chart.volumeLots': 'Volume (lots)',
  'chart.lots': '{value} lots',

  // ---------- admin: users ----------
  'adminUsers.title': 'User management',
  'adminUsers.searchPlaceholder': 'Search username or email',
  'adminUsers.pendingOnly': 'Awaiting approval only',
  'adminUsers.pendingBanner':
    '{count} account(s) are waiting for approval. Approve one to let that person sign in.',
  'adminUsers.approve': 'Approve',
  'adminUsers.lockedUntil': 'Locked until {when}',
  'adminUsers.unlock': 'Unlock',
  'adminUsers.opFailed': 'Action failed: {message}',
  'adminUsers.colUsername': 'Username',
  'adminUsers.colEmail': 'Email',
  'adminUsers.colPhone': 'Phone',
  'adminUsers.colRole': 'Role',
  'adminUsers.colStatus': 'Status',
  'adminUsers.colPassword': 'Password',
  'adminUsers.colCreatedAt': 'Registered',
  'adminUsers.selfRoleTitle': 'You cannot change your own role',
  'adminUsers.statusActive': 'Active',
  'adminUsers.statusInactive': 'Disabled',
  'adminUsers.pendingReset': 'Reset pending',
  'adminUsers.selfPasswordTitle': 'To change your own password use “Change password”',
  'adminUsers.resetPassword': 'Reset password',
  'adminUsers.resetConfirm':
    'Reset the password for {username}?\n\nA temporary password will be generated and shown only once. Every existing session for that account is invalidated, and the user must set a new password before anything else works.',
  'adminUsers.delete': 'Delete',
  'adminUsers.deleteConfirm':
    'Delete {username}? Their watchlist is removed along with the account.',
  'adminUsers.footer':
    '{total} accounts. At least one active ADMIN must remain, and you cannot change your own role or status. “Reset pending” means the account is holding a temporary password and can do nothing but set a new one.',
  'adminUsers.tempIssuedFor':
    'A temporary password has been generated for {username} ({email}).',
  'adminUsers.tempIssuedOnce':
    'It is shown this once and cannot be looked up again — pass it on through a trusted channel now.',
  'adminUsers.copy': 'Copy',
  'adminUsers.copied': 'Copied',
  'adminUsers.dismiss': 'Got it, close',
  'adminUsers.tempIssuedNote':
    'After signing in they must set their own password before anything else works; every session that account had is already invalidated.',

  // ---------- background jobs: shared vocabulary (utils/jobs.ts) ----------
  'jobs.statusSuccess': 'Success',
  'jobs.statusSkipped': 'Skipped',
  'jobs.statusFailed': 'Failed',
  'jobs.triggerStartup': 'Startup',
  'jobs.triggerSchedule': 'Schedule',
  'jobs.triggerManual': 'Manual',
  'jobs.neverRun': 'Never run',
  'jobs.neverSucceeded': 'Never succeeded',
  'jobs.justNow': 'just now',
  'jobs.minutesAgo': '{count} min ago',
  'jobs.hoursAgo': '{count} h ago',
  'jobs.daysAgo': '{count} d ago',
  'jobs.scheduleOff': 'Disabled',
  'jobs.dueNow': 'due now',
  'jobs.inMinutes': 'in ~{count} min',
  'jobs.inHours': 'in ~{count} h',
  'jobs.inDays': 'in ~{count} d',
  'jobs.everyMinutes': 'every {count} min',
  'jobs.everyHours': 'every {count} h',
  'jobs.everyDays': 'every {count} d',
  'jobs.dailyAt': 'daily at {time} ({timezone})',
  'jobs.errorRunning': 'This job is already running — wait for it to finish and try again',
  'jobs.errorCooldown':
    'It was run manually a moment ago. Try again shortly (this keeps the upstream from being hammered).',

  // ---------- background jobs: console ----------
  'jobs.title': 'Scheduled jobs',
  'jobs.timezone': 'Timezone {timezone}',
  'jobs.loadFailed': 'Could not load: {message}',
  'jobs.schedulerOffBefore': 'The scheduler is not running in this process (',
  'jobs.schedulerOffAfter':
    '), so every job below can only be run by hand. Running several replicas with the scheduler on exactly one of them is a normal setup.',
  'jobs.footer':
    '“Skipped” means the job woke up and confirmed there was nothing to do — it is the scheduler’s heartbeat, not a failure. Manual runs are recorded along with who pressed the button.',
  'jobs.running': 'running',
  'jobs.runNow': 'Run now',
  'jobs.runNowTitle':
    'Takes about {seconds}s; one job can only be run by hand once every {cooldown}s',
  'jobs.runHistory': 'Run history',
  'jobs.started': 'Started — about {seconds}s, and the result will appear in the run history',
  'jobs.startedBelow':
    'Started — about {seconds}s, and the result will appear in the log below',
  'jobs.dotDisabled': 'Schedule disabled',
  'jobs.dotStale': 'No successful run for too long',
  'jobs.dotOk': 'Healthy',
  'jobs.staleNotice':
    'No successful run for more than two schedule cycles. Check the failure reason in the run history.',
  'jobs.staleNoticeSince':
    'No successful run for more than two schedule cycles (last one {since}). Check the failure reason in the run history.',
  'jobs.statSchedule': 'Schedule',
  'jobs.statNextRun': 'Next run',
  'jobs.statLastResult': 'Last result',
  'jobs.statLastSuccess': 'Last success',
  'jobs.statRuns': 'Log entries',
  'jobs.back': '← Scheduled jobs',
  'jobs.detailFooter':
    'Only the last 200 runs are kept per job. Manual runs record which administrator pressed the button.',

  // ---------- background jobs: run table ----------
  'jobRuns.colStarted': 'Started',
  'jobRuns.colTrigger': 'Trigger',
  'jobRuns.colResult': 'Result',
  'jobRuns.colDuration': 'Duration',
  'jobRuns.colMessage': 'Message',
  'jobRuns.empty': 'No run has been recorded yet',

  // ---------- background jobs: schedule editor ----------
  'schedule.unitMinutes': 'minutes',
  'schedule.unitHours': 'hours',
  'schedule.unitDays': 'days',
  'schedule.rangeHint': 'Allowed range {min} – {max}',
  'schedule.amount': '{value} {unit}',
  'schedule.enabled': 'Schedule on',
  'schedule.disabled': 'Schedule off',
  'schedule.disableTitle': 'Once disabled this job will not run on its own',
  'schedule.enableTitle': 'Turn the schedule back on',
  'schedule.kindInterval': 'Fixed interval',
  'schedule.kindDaily': 'Daily at',
  'schedule.save': 'Save schedule',
  'schedule.timezoneNote': 'Interpreted in {timezone}',
  'schedule.defaultNote':
    ' · currently the default from the environment; saving here takes over',
  'schedule.updatedBy': ' · last changed by {user} on {time}',
  'schedule.outOfRange':
    'This interval is outside the allowed range and the server will refuse it. Jobs that hit the exchange, such as the listing sync, get blocked upstream if run too often.',
  'schedule.saveFailed': 'The schedule was not saved: {message}',

  // ---------- admin: listing sync ----------
  'adminCodes.title': 'Listed instrument sync',
  'adminCodes.syncing': 'Fetching, about {seconds}s…',
  'adminCodes.scheduleSettings': 'Schedule settings',
  'adminCodes.syncNow': 'Sync now',
  'adminCodes.opFailed': 'Action failed: {message}',
  'adminCodes.started':
    'Sync started — about {seconds}s, and the result will appear in the log below.',
  'adminCodes.scheduleOffBefore':
    'The schedule for this job is disabled, so newly listed instruments will not be picked up. Turn it back on under ',
  'adminCodes.scheduleOffAfter': '.',
  'adminCodes.staleNotice':
    'No successful sync for more than two schedule cycles ({since}). Newly listed instruments cannot be found right now — check the failure reason in the log below.',
  'adminCodes.neverSyncedNotice':
    'The listing has never been reconciled with the exchange. The bundled twstock snapshot is in use, so anything listed after that snapshot is missing.',
  'adminCodes.statActive': 'Searchable instruments',
  'adminCodes.statLastSuccess': 'Last successful sync',
  'adminCodes.statSchedule': 'Schedule',
  'adminCodes.statNextRun': 'Next run',
  'adminCodes.statRuns': 'Log entries',
  'adminCodes.noRuns': 'No sync has run yet',
  'adminCodes.footer':
    'Only the last 200 runs are kept. “Skipped” means the scheduler woke up while the listing was still within its interval and had nothing to do — it is this batch job’s heartbeat. Runs whose message says Partial had only one market answer: what came back was written, but nothing was retired on purpose. Delisted instruments are never deleted and stay chartable by their full code; only call/put warrants expired for more than 30 days are pruned.',

  // ---------- vocabulary the API sends back (see i18n/serverText.ts) ----------
  'bfpReason.buyHeavyVolumeUp': 'Heavy volume, closed up',
  'bfpReason.buyVolumeDownPriceHeld': 'Volume shrank, price held',
  'bfpReason.buyMa3TurnedUp': '3-day average turning up',
  'bfpReason.buyMa3AboveMa6': '3-day average above the 6-day',
  'bfpReason.sellHeavyVolumeDown': 'Heavy volume, closed down',
  'bfpReason.sellVolumeDownPriceFell': 'Volume shrank, price fell',
  'bfpReason.sellMa3TurnedDown': '3-day average turning down',
  'bfpReason.sellMa3BelowMa6': '3-day average below the 6-day',
  'bfpReason.needSamples': 'At least {count} trading days are needed to judge',
  'bfpReason.buyNotOversold':
    'No buy point: not consistently oversold over the last 5 days (the 3-day average was not below the 6-day throughout)',
  'bfpReason.buyStillBottoming':
    'No buy point: the bias is still bottoming out (the trough is today, so the turn is unconfirmed)',
  'bfpReason.buyWindowPassed':
    'No buy point: the oversold trough was not yesterday or the day before — the turn window has passed',
  'bfpReason.sellNotOverbought':
    'No sell point: not overbought over the last 5 days (the 3-day average never rose above the 6-day)',
  'bfpReason.sellStillRising':
    'No sell point: the bias is still climbing (the peak is today, so the turn is unconfirmed)',
  'bfpReason.sellWindowPassed':
    'No sell point: the overbought peak was not yesterday or the day before — the turn window has passed',
  'bfpReason.noBuyConditions': 'None of the four buy conditions is met',
  'bfpReason.noSellConditions': 'None of the four sell conditions is met',
  'bfpReason.biasPassedNoBuy':
    'The buy-side bias gate passed, but none of the four buy conditions is met',
  'bfpReason.biasPassedNoSell':
    'The sell-side bias gate passed, but none of the four sell conditions is met',
  'bfpLabel.insufficient': 'Not enough data',
  'bfpReason.noDailyBars': 'No daily bars yet — open the stock page to load them',
  'stockMarket.twse': 'TWSE',
  'stockMarket.tpex': 'TPEx',
  'stockType.stock': 'Stock',
  'stockType.etf': 'ETF',
  'stockType.etn': 'ETN',
  'stockType.warrantTwse': 'TWSE warrant',
  'stockType.warrantTpex': 'TPEx warrant',
  'stockType.beneficiary': 'Beneficiary certificate',
  'jobName.stock_code_sync': 'Listed instrument sync',
  'jobDesc.stock_code_sync':
    'Re-fetches TWSE/TPEx codes from the exchange ISIN listing into stock_code. Without it, newly listed instruments simply cannot be found.',
  'jobName.refresh_token_cleanup': 'Refresh-token cleanup',
  'jobDesc.refresh_token_cleanup':
    'Deletes expired refresh tokens, and revoked ones past their retention window. Revocations inside the window must be kept — replay detection is what catches a stolen token.',
  'jobStat.inserted': 'Added',
  'jobStat.updated': 'Updated',
  'jobStat.delisted': 'Delisted',
  'jobStat.pruned': 'Pruned',
  'jobStat.active': 'Searchable',
  'jobStat.deleted': 'Deleted',
}
