<?xml version="1.0" encoding="UTF-8"?>
<!-- Erynne's look for RD Web Access (remote_apps.xsl_override = 'rdweb'; remote.py OVERRIDES).

     Mnemerynne runs this in place of Microsoft's Pages/Site.xsl, at the same address, so the files it
     reads relative to itself (RDWAStrings.xml through $baseurl, the scripts beside it) still come from
     RD Web through the proxy. RD Web keeps doing the work: it signs people in, lists their apps and
     hands out the .rdp files. Only the drawing changes.

     What must survive from Microsoft's stylesheet, or RD Web stops working:
       - the page's scripts: ../renderscripts.js, ../webscripts-domain.js, HeaderJS, PostHtmlLoadJS and
         the BodyAttr handlers (sign-in checks, the idle sign-out timers, the TSWA cookies);
       - HTMLMainContent copied as it is: the sign-in form (FrmLogin and its field names), the password
         and error pages. Its scripts find rows by id and show them with style.display = "";
       - FrmUserInfo with the DomainUserName hidden input (onAuthenticated reads it);
       - the tab ids (the page's own Style hides #PORTAL_REMOTE_DESKTOPS when it can't be used);
       - each app's .rdp link. The ActiveX launcher (IE only) is gone: Launch downloads the .rdp.

     $theme comes from remote.look_params: 'default' (Sakura) or 'neon'. -->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:appfeed="http://schemas.microsoft.com/ts/2007/05/tswf"
                xmlns:str="urn:microsoft.com:rdwastrings"
                exclude-result-prefixes="appfeed str">
  <!-- HTML5 in standards mode (about:legacy-compat is XSLT 1.0's way to write <!DOCTYPE html>). -->
  <xsl:output method="html" encoding="UTF-8" doctype-system="about:legacy-compat"/>

  <xsl:param name="theme" select="'default'"/>                                   <!-- set by the proxy -->

  <xsl:variable name="baseurl" select="/RDWAPage/@baseurl"/>                    <!-- e.g. /remote/collab-pc/Pages/en-US/ -->
  <xsl:variable name="strings" select="document(concat($baseurl, 'RDWAStrings.xml'))/str:strings/string"/>
  <xsl:variable name="publisher" select="/RDWAPage/AppFeed[1]/appfeed:ResourceCollection/appfeed:Publisher[1]"/>
  <xsl:variable name="themename">                                                <!-- only the two this sheet knows -->
    <xsl:choose>
      <xsl:when test="$theme = 'neon'">neon</xsl:when>
      <xsl:otherwise>default</xsl:otherwise>
    </xsl:choose>
  </xsl:variable>

  <!-- ============================================================== the page -->
  <xsl:template match="/RDWAPage">
    <html lang="en" data-theme="{$themename}">
      <head>
        <xsl:if test="$baseurl">
          <base href="{$baseurl}"/>                                              <!-- RD Web's links are relative to this -->
        </xsl:if>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <meta name="robots" content="noindex, nofollow"/>
        <title>
          <xsl:call-template name="text">
            <xsl:with-param name="id" select="'PageTitle'"/>
            <xsl:with-param name="otherwise" select="'RD Web Access'"/>
          </xsl:call-template>
        </title>
        <style><xsl:value-of select="$css"/></style>
        <!-- The page's own styles (not IE's conditional ones): they hide what can't be used here. -->
        <xsl:for-each select="Style[not(@condition)]">
          <style><xsl:value-of select="."/></style>
        </xsl:for-each>
        <script type="text/javascript" src="../renderscripts.js"></script>
        <script type="text/javascript" src="../webscripts-domain.js"></script>
        <script type="text/javascript">
          var sHelpSource = "<xsl:value-of select="@helpurl"/>";
          <xsl:value-of select="HeaderJS[1]"/>
          <xsl:if test="$baseurl">strBaseUrl = "<xsl:value-of select="$baseurl"/>";</xsl:if>
        </script>
      </head>

      <body>
        <xsl:apply-templates select="BodyAttr/@*"/>                              <!-- onload, onunload, activity timers -->
        <noscript><xsl:copy-of select="$strings[@id = 'NoScriptWarning']/node()"/></noscript>
        <xsl:apply-templates select="@domainuser"/>                              <!-- FrmUserInfo -->

        <div class="rdw">
          <header class="rdw-bar">
            <div class="rdw-brand">
              <span class="rdw-mark" aria-hidden="true">&#x2756;</span>
              <div>
                <strong><xsl:value-of select="@workspacename"/></strong>
                <small>
                  <xsl:call-template name="text">
                    <xsl:with-param name="id" select="'HeadingApplicationName'"/>
                    <xsl:with-param name="otherwise" select="'RemoteApp and Desktop Connection'"/>
                  </xsl:call-template>
                </small>
              </div>
            </div>
            <xsl:apply-templates select="NavBar[1]/Tab"/>
            <div class="rdw-user">
              <xsl:if test="@domainuser">
                <span class="rdw-who" title="{@domainuser}">
                  <xsl:call-template name="short-user"><xsl:with-param name="user" select="@domainuser"/></xsl:call-template>
                </span>
              </xsl:if>
              <a id="PORTAL_HELP" href="javascript:onClickHelp()">
                <xsl:call-template name="text">
                  <xsl:with-param name="id" select="'Help'"/>
                  <xsl:with-param name="otherwise" select="'Help'"/>
                </xsl:call-template>
              </a>
              <xsl:if test="NavBar[1]/@showsignout = 'true'">
                <a id="PORTAL_SIGNOUT" class="rdw-signout" href="javascript:onUserDisconnect()" target="_self">
                  <xsl:call-template name="text">
                    <xsl:with-param name="id" select="'SignOut'"/>
                    <xsl:with-param name="otherwise" select="'Sign out'"/>
                  </xsl:call-template>
                </a>
              </xsl:if>
            </div>
          </header>

          <main class="rdw-main">
            <xsl:choose>
              <xsl:when test="HTMLMainContent[1]">                               <!-- sign-in, password, errors, remote PC -->
                <section class="rdw-card rdw-form">
                  <xsl:copy-of select="HTMLMainContent[1]/node()"/>
                </section>
              </xsl:when>
              <xsl:when test="AppFeed[1]">                                       <!-- the app list -->
                <xsl:call-template name="apps"/>
              </xsl:when>
            </xsl:choose>
            <xsl:if test="ExtraRows[1]/*">                                        <!-- extra rows some pages add -->
              <section class="rdw-card"><table class="rdw-extra"><xsl:copy-of select="ExtraRows[1]/*"/></table></section>
            </xsl:if>
          </main>
        </div>

        <script type="text/javascript">
          <xsl:value-of select="PostHtmlLoadJS[1]"/>
        </script>
      </body>
    </html>
  </xsl:template>

  <!-- body onload="..." and friends, copied onto <body> -->
  <xsl:template match="/RDWAPage/BodyAttr/@*">
    <xsl:attribute name="{name(.)}"><xsl:value-of select="."/></xsl:attribute>
  </xsl:template>

  <!-- Who is signed in, where RD Web's scripts look for it (onAuthenticated). -->
  <xsl:template match="/RDWAPage/@domainuser">
    <form name="FrmUserInfo" id="FrmUserInfo">
      <input type="hidden" id="DomainUserName" name="DomainUserName" value="{.}"/>
    </form>
  </xsl:template>

  <!-- One tab of the navigation bar: the open one as text, the others as links; ids kept. -->
  <xsl:template match="/RDWAPage/NavBar/Tab">
    <xsl:choose>
      <xsl:when test="../@activetab = @id">
        <span class="rdw-tab rdw-tab-on" id="{@id}" aria-current="page"><xsl:value-of select="."/></span>
      </xsl:when>
      <xsl:otherwise>
        <a class="rdw-tab" id="{@id}" href="{@href}" target="_self"><xsl:value-of select="."/></a>
      </xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- ============================================================== the app list -->
  <xsl:template name="apps">
    <xsl:variable name="folder" select="$publisher/@DisplayFolder"/>
    <section class="rdw-apps">
      <h1>
        <xsl:choose>
          <xsl:when test="$folder and $folder != '/'">
            <xsl:value-of select="substring-after($folder, '/')"/>                <!-- inside a folder -->
          </xsl:when>
          <xsl:otherwise>Apps</xsl:otherwise>
        </xsl:choose>
      </h1>
      <div class="rdw-grid">
        <xsl:if test="$folder and $folder != '/'">                              <!-- back to the top -->
          <a class="rdw-app rdw-up" href="Default.aspx" target="_self">
            <span class="rdw-icon rdw-glyph" aria-hidden="true">&#x2190;</span>
            <span class="rdw-name">
              <xsl:call-template name="text">
                <xsl:with-param name="id" select="'ParentFolder'"/>
                <xsl:with-param name="otherwise" select="'Up'"/>
              </xsl:call-template>
            </span>
          </a>
        </xsl:if>
        <xsl:apply-templates select="$publisher/appfeed:SubFolders[1]/appfeed:Folder">
          <xsl:sort select="@Name"/>
        </xsl:apply-templates>
        <xsl:apply-templates select="$publisher/appfeed:Resources[1]/appfeed:Resource">
          <xsl:sort select="@Title"/>
        </xsl:apply-templates>
      </div>
      <xsl:if test="not($publisher/appfeed:Resources[1]/appfeed:Resource) and not($publisher/appfeed:SubFolders[1]/appfeed:Folder)">
        <p class="rdw-empty">No apps or desktops are published for you here.</p>
      </xsl:if>
      <p class="rdw-hint">Launch downloads a small <code>.rdp</code> file. Open it and Remote Desktop Connection starts the app.</p>
    </section>
    <script type="text/javascript">
      // Folder cards: RD Web wants the folder name URL-encoded after Default.aspx/ (XSLT 1.0 can't encode).
      (function () {
        var cards = document.querySelectorAll("a[data-rdw-folder]");
        for (var i = 0; i &lt; cards.length; i++) {
          cards[i].href = strBaseUrl + "Default.aspx/" + encodeURIComponent(cards[i].getAttribute("data-rdw-folder"));
        }
      })();
      // Resources that aren't .rdp files carry an escaped address to open instead.
      function rdwOpen(link) {
        location.href = unescape(link.getAttribute("data-rdw-open"));
        return false;
      }
    </script>
  </xsl:template>

  <!-- One app or desktop: a card that downloads its .rdp. -->
  <xsl:template match="appfeed:Resource">
    <xsl:variable name="file" select="appfeed:HostingTerminalServers/appfeed:HostingTerminalServer[1]/appfeed:ResourceFile"/>
    <xsl:variable name="icon" select="appfeed:Icons/appfeed:Icon32[@FileType = 'Png'][1]/@FileURL"/>
    <a class="rdw-app" title="{@Title}" target="_self">
      <xsl:choose>
        <xsl:when test="$file/@FileExtension = '.rdp' and $file/@URL">
          <xsl:attribute name="href"><xsl:value-of select="$file/@URL"/></xsl:attribute>   <!-- /remote/<slug>/.../x.rdp -->
          <xsl:attribute name="download"/>
        </xsl:when>
        <xsl:otherwise>
          <xsl:attribute name="href">#</xsl:attribute>
          <xsl:attribute name="data-rdw-open"><xsl:value-of select="$file/appfeed:Content"/></xsl:attribute>
          <xsl:attribute name="onclick">return rdwOpen(this)</xsl:attribute>
        </xsl:otherwise>
      </xsl:choose>
      <xsl:choose>
        <xsl:when test="$icon">
          <img class="rdw-icon" src="{$icon}" alt="" width="32" height="32"/>
        </xsl:when>
        <xsl:otherwise>
          <span class="rdw-icon rdw-glyph" aria-hidden="true">&#x25A3;</span>       <!-- no icon published -->
        </xsl:otherwise>
      </xsl:choose>
      <span class="rdw-name"><xsl:value-of select="@Title"/></span>
      <span class="rdw-kind">
        <xsl:choose>
          <xsl:when test="@Type = 'Desktop'">Desktop</xsl:when>
          <xsl:otherwise>App</xsl:otherwise>
        </xsl:choose>
      </span>
      <span class="rdw-launch">Launch</span>
    </a>
  </xsl:template>

  <!-- A folder of apps (its Name is "/Folder"); the script in "apps" fills in the address. -->
  <xsl:template match="appfeed:Folder">
    <xsl:variable name="name" select="substring-after(@Name, '/')"/>
    <a class="rdw-app rdw-folder" href="Default.aspx" data-rdw-folder="{$name}" title="{$name}" target="_self">
      <span class="rdw-icon rdw-glyph" aria-hidden="true">&#x25A4;</span>
      <span class="rdw-name"><xsl:value-of select="$name"/></span>
      <span class="rdw-kind">Folder</span>
    </a>
  </xsl:template>

  <!-- ============================================================== helpers -->
  <!-- A string from RDWAStrings.xml, or the English fallback when it couldn't be read. -->
  <xsl:template name="text">
    <xsl:param name="id"/>
    <xsl:param name="otherwise"/>
    <xsl:variable name="found" select="normalize-space($strings[@id = $id])"/>
    <xsl:choose>
      <xsl:when test="$found"><xsl:value-of select="$found"/></xsl:when>
      <xsl:otherwise><xsl:value-of select="$otherwise"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- DOMAIN\name shows as name (the full form stays in the tooltip). -->
  <xsl:template name="short-user">
    <xsl:param name="user"/>
    <xsl:choose>
      <xsl:when test="contains($user, '\')"><xsl:value-of select="substring-after($user, '\')"/></xsl:when>
      <xsl:otherwise><xsl:value-of select="$user"/></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <!-- ============================================================== the styles -->
  <!-- Erynne's tokens (web/static/erynne.css :root and theme-neon.css), kept here so the page needs
       nothing else from Mnemerynne but the Sakura picture. The second half dresses the markup RD Web
       sends in HTMLMainContent (fixed-width tables, .wrng, .expl, .textInputField, .formButton). -->
  <xsl:variable name="css">
:root {
  --blush: #fbeef0; --rose: #c25a72; --rose-deep: #9c4157; --ink: #3d2530; --muted: #8a6b74;
  --glass: rgba(255, 250, 251, 0.82); --glass-strong: rgba(255, 252, 253, 0.94);
  --line: rgba(194, 90, 114, 0.22); --shadow: 0 8px 30px rgba(156, 65, 87, 0.12);
  --error: #b3263e; --bad-bg: #fde2e6; --scene-tint: rgba(253, 240, 243, 0.45); --radius: 14px;
  --on-rose: #fff;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Noto Serif", serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  html[data-theme="default"] {
    --rose: #f08aa2; --rose-deep: #f6a9bb; --ink: #f6e7eb; --muted: #c8a9b2;
    --glass: rgba(43, 24, 34, 0.82); --glass-strong: rgba(48, 27, 38, 0.94);
    --line: rgba(240, 138, 162, 0.24); --shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
    --error: #ff9fb0; --bad-bg: #4a1f2a; --scene-tint: rgba(28, 12, 22, 0.66); --on-rose: #2b0f1a;
  }
}
html[data-theme="neon"] {
  color-scheme: dark;
  --blush: #07020d; --rose: #ff2e97; --rose-deep: #ff7ac0; --ink: #ffe9f5; --muted: #b98aa9;
  --glass: rgba(12, 3, 20, 0.86); --glass-strong: rgba(18, 5, 28, 0.95); --line: rgba(255, 46, 151, 0.5);
  --shadow: 0 0 0 1px rgba(255, 46, 151, 0.35), -2px 0 0 rgba(0, 240, 255, 0.35), 2px 0 0 rgba(255, 20, 80, 0.45),
            0 0 26px rgba(255, 46, 151, 0.28);
  --error: #ff4d6d; --bad-bg: rgba(255, 45, 85, 0.16); --radius: 0; --on-rose: #0a0010;
  --serif: "Bahnschrift", "DIN Alternate", "Eurostile", "Roboto Condensed", "Arial Narrow", system-ui, sans-serif;
}

* { box-sizing: border-box; }
html { min-height: 100%; }
body {
  margin: 0; min-height: 100vh; color: var(--ink); font: 15px/1.5 var(--sans);
  background-color: var(--blush);
  background-image: linear-gradient(var(--scene-tint), var(--scene-tint)), url("/static/sakura.jpg");
  background-size: cover; background-position: center bottom; background-attachment: fixed;
}
html[data-theme="neon"] body {
  background-image:
    radial-gradient(ellipse 70% 45% at 50% 108%, rgba(255, 46, 151, 0.42), transparent 70%),
    radial-gradient(ellipse 45% 35% at 88% 6%, rgba(0, 240, 255, 0.13), transparent 70%),
    linear-gradient(180deg, #07020d 0%, #12031f 60%, #1d0428 100%);
}
a { color: var(--rose); }
a:hover { color: var(--rose-deep); }
noscript p { margin: 1rem; padding: .75rem 1rem; background: var(--bad-bg); color: var(--error); }

.rdw { max-width: 60rem; margin: 0 auto; padding: 1rem; }

/* the bar: workspace, tabs, who is signed in */
.rdw-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: .5rem 1rem;
  padding: .7rem 1rem; margin-bottom: 1rem;
  background: var(--glass-strong); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
  backdrop-filter: blur(8px);
}
.rdw-brand { display: flex; align-items: center; gap: .6rem; margin-right: auto; }
.rdw-brand strong { display: block; font-family: var(--serif); font-size: 1.15rem; font-weight: 600; }
.rdw-brand small { display: block; color: var(--muted); font-size: .78rem; }
.rdw-mark { color: var(--rose); font-size: 1.4rem; line-height: 1; }
.rdw-tab { padding: .3rem .7rem; border-radius: 999px; text-decoration: none; font-size: .9rem; color: var(--ink); }
.rdw-tab:hover { background: var(--line); color: var(--ink); }
.rdw-tab-on { background: var(--rose); color: var(--on-rose); }
.rdw-user { display: flex; align-items: center; gap: .8rem; font-size: .88rem; }
.rdw-who { color: var(--muted); }
.rdw-signout { font-weight: 600; }

/* cards */
.rdw-card, .rdw-apps {
  background: var(--glass); border: 1px solid var(--line); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 1.25rem; backdrop-filter: blur(8px);
}
.rdw-apps h1 { margin: 0 0 1rem; font-family: var(--serif); font-size: 1.35rem; font-weight: 600; }
.rdw-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); gap: .8rem; }
.rdw-app {
  display: flex; flex-direction: column; align-items: center; gap: .35rem; padding: 1rem .6rem .8rem;
  text-align: center; text-decoration: none; color: var(--ink);
  background: var(--glass-strong); border: 1px solid var(--line); border-radius: var(--radius);
  transition: transform .12s ease, border-color .12s ease;
}
.rdw-app:hover, .rdw-app:focus-visible { color: var(--ink); border-color: var(--rose); transform: translateY(-2px); outline: none; }
.rdw-icon { width: 40px; height: 40px; object-fit: contain; image-rendering: auto; }
.rdw-glyph { display: grid; place-items: center; font-size: 1.6rem; color: var(--rose); }
.rdw-name { font-weight: 600; line-height: 1.25; overflow-wrap: anywhere; }
.rdw-kind { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; }
.rdw-launch {
  margin-top: .3rem; padding: .25rem .9rem; border-radius: 999px; font-size: .82rem; font-weight: 600;
  background: var(--rose); color: var(--on-rose);
}
.rdw-app:hover .rdw-launch { background: var(--rose-deep); }
.rdw-empty { color: var(--muted); }
.rdw-hint { margin: 1rem 0 0; color: var(--muted); font-size: .82rem; }

/* RD Web's own forms (HTMLMainContent): undo the fixed widths and the 2008 look */
.rdw-form { max-width: 26rem; margin: 0 auto; }
.rdw-main table { width: 100% !important; border-collapse: collapse; }
.rdw-main td { padding: 0; vertical-align: middle; text-align: left; }
.rdw-main td[bgcolor] { background: var(--line) !important; }
.rdw-main td[height="20"], .rdw-main td[height="30"] { height: .6rem; }
.rdw-main td[width="130"] { width: 9.5rem; white-space: nowrap; padding-right: .75rem; color: var(--muted); font-size: .9rem; }
.rdw-main td[width="7"] { display: none; }
.rdw-main td[align="right"]:has(.formButton) { text-align: right; }
.rdw-main input[type="text"], .rdw-main input[type="password"], .rdw-main .textInputField {
  width: 100%; padding: .55rem .7rem; font: inherit; color: var(--ink);
  background: var(--glass-strong); border: 1px solid var(--line); border-radius: calc(var(--radius) * .6);
}
.rdw-main input[type="text"]:focus, .rdw-main input[type="password"]:focus {
  outline: none; border-color: var(--rose); box-shadow: 0 0 0 3px var(--line);
}
.rdw-main input[type="radio"], .rdw-main input[type="checkbox"] { accent-color: var(--rose); }
.rdw-main .formButton, .rdw-main input[type="submit"], .rdw-main input[type="button"] {
  padding: .55rem 1.6rem; font: inherit; font-weight: 600; cursor: pointer;
  color: var(--on-rose); background: var(--rose); border: 0; border-radius: 999px;
}
.rdw-main .formButton:hover, .rdw-main input[type="submit"]:hover { background: var(--rose-deep); }
.rdw-main .wrng {
  display: block; padding: .5rem .75rem; color: var(--error); background: var(--bad-bg);
  border-radius: calc(var(--radius) * .6); font-size: .9rem;
}
.rdw-main .expl { color: var(--muted); font-size: .85rem; }

@media (max-width: 34rem) {
  .rdw { padding: .5rem; }
  .rdw-bar { padding: .6rem .75rem; }
  .rdw-grid { grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr)); }
  .rdw-main td[width="130"] { display: block; width: auto; padding: 0 0 .25rem; }
  .rdw-main td[width="130"] + td + td { display: block; }
}
  </xsl:variable>
</xsl:stylesheet>
