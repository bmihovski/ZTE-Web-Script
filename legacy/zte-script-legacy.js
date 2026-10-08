/*
 * 
 * Original code by Miononno
 * https://www.youtube.com/watch?v=1kanq1w2DA0
 * 
 * Enhanced by unknown @ lteforum.at
 * 
 */

console.log("Loading ZTE Script v" + "2026-10-08-#4");

siginfo =
    "wan_active_band,wan_active_channel,wan_lte_ca,wan_apn,wan_ipaddr," +
    "cell_id,dns_mode,prefer_dns_manual,standby_dns_manual,network_type," +

    "network_provider_fullname," +
    "rmcc,rmnc," +

    "ip_passthrough_enabled," +

    "bandwidth," +
    "tx_power," +

    "rscp_1,ecio_1,rscp_2,ecio_2,rscp_3,ecio_3,rscp_4,ecio_4,ecio," +

    "ngbr_cell_info," +
    "nr5g_nsa_bandwidth,Z5g_rssi,lte_ta,system_uptime,realtime_rx_thrpt,realtime_tx_thrpt,pm_modem_5g," +
    "signal_quality,total_rx_bytes,total_tx_bytes,total_time," +
    "lte_multi_ca_scell_info,lte_multi_ca_scell_sig_info," +
    "lte_band,lte_rsrp,lte_rsrq," +
    "lte_rsrq,lte_rssi,lte_rsrp,lte_snr," +
    "lte_ca_pcell_band,lte_ca_pcell_freq,lte_ca_pcell_bandwidth," +
    "lte_ca_scell_band,lte_ca_scell_bandwidth," +
    "lte_rsrp_1,lte_rsrp_2,lte_rsrp_3,lte_rsrp_4," +
    "lte_snr_1,lte_snr_2,lte_snr_3,lte_snr_4," +
    "lte_pci,lte_pci_lock,lte_earfcn_lock,lte_band_lock,nr5g_cell_lock," +

    "5g_rx0_rsrp,5g_rx1_rsrp,Z5g_rsrp,Z5g_rsrq,Z5g_SINR," +
    "nr5g_cell_id,nr5g_pci," +
    "nr5g_action_channel,nr5g_action_band," +
    "nr5g_action_nsa_band," +
    "nr_ca_pcell_band,nr_ca_pcell_freq," +
    "nr_multi_ca_scell_info," +
    "nr5g_sa_band_lock,nr5g_nsa_band_lock," +

    "pm_sensor_ambient,pm_sensor_mdm,pm_sensor_5g,pm_sensor_pa1,wifi_chip_temp";

is_mc888 = false;
is_mc889 = false;
logged_in_as_developer = false;

function dump_variable(v)
{
    for (property in v)
    {
        try
        {
            console.log(property + ":" + JSON.stringify(v[property]));
        }
        catch { }
    }
}

function var2html(prefix, v)
{
    for (index in v)
    {
        var items = v[index];
    
        for (item_index in items)
            $("#" + prefix + "_" + index + "_" + item_index).html(items[item_index]);
    }
}

function test_cmd(cmd)
{
    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: cmd,
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            console.log(a);
        }
    });
}

// https://stackoverflow.com/a/68009748/1392778
window.cookies = window.cookies || 
{
    // https://stackoverflow.com/a/25490531/1028230
    get: function(name)
    {
        var b = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return b ? b.pop() : null;
    },

    delete: function(name)
    {
        document.cookie = '{0}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;'
            .replace('{0}', name);
    },

    set: function(name, value)
    {
        document.cookie =
            '{0}={1};expires=Fri, 31 Dec 9999 23:59:59 GMT;path=/;SameSite=Lax'
            .replace('{0}', name)
            .replace('{1}', value);
    }
};

function show_logout_and_shutdown_buttons()
{
    document.getElementById("logout").childNodes.forEach(el => {
        $(el).hide();
        $(el).show();
    });
}

wait_for_log_in_done = false;
function wait_for_log_in()
{
    check_log_in(
        function()
        {
            if (wait_for_log_in_done) return;
            wait_for_log_in_done = true;

            inject_html();
            get_status();
            load_device_extras();
            positions_render();
            exp_render();

            show_logout_and_shutdown_buttons_i = 0;
            show_logout_and_shutdown_buttons_timer_id = window.setInterval(function() {
                show_logout_and_shutdown_buttons();
                if (++show_logout_and_shutdown_buttons_i >= 6)
                    window.clearInterval(show_logout_and_shutdown_buttons_timer_id);
            }, 500);

            show_logout_and_shutdown_buttons();
        
            window.setInterval(get_status, 1000);
            window.setInterval(prevent_automatic_logout, 60000);

            window.clearInterval(wait_for_log_in_timer_id);
        },

        function()
        {
            if (typeof show_log_in_info_once === "undefined")
                console.log("Contents of script will show once you are logged in!");
            show_log_in_info_once = true;
        }
    );
}

function init()
{
    wait_for_log_in_timer_id = window.setInterval(wait_for_log_in, 250);
    wait_for_log_in();
}

function perform_automatic_login_or_init()
{
    if (have_admin_password_hash())
    {
        check_log_in(
        
            function()
            {
                console.log("Already logged in ...");
                init();
            },

            function()
            {
                console.log("Logging in ...");
                perform_login(function() {
                    console.log("... logged in");
                    init();
                    hash_fix_i = 0;
                    hash_fix_timer_id = window.setInterval(function() {
                        window.location.hash = "home";
                        if (++hash_fix_i >= 10) window.clearInterval(hash_fix_timer_id);
                    }, 100);
                });
            }
        
        );
    }
    else init();
}

/*
 * Wait until inner version string is available.
 */
prepare_2_done = false;
function prepare_2()
{
    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version"
        },
        dataType: "json",
        success: function(a)
        {
            if (a.wa_inner_version == "" || prepare_2_done) return;
            prepare_2_done = true;

            is_mc888 = a.wa_inner_version.indexOf("MC888") > -1;
            is_mc889 = a.wa_inner_version.indexOf("MC889") > -1;

            if (is_mc888 || is_mc889) hash = SHA256;
            else hash = hex_md5;

            perform_automatic_login_or_init();

            window.clearInterval(prepare_2_timer_id);
        }
    })
}

/*
 * Wait until SHA256() is available.
 */
function prepare_1()
{
    if (typeof SHA256 === "undefined")
    {
        return;
    }

    window.clearInterval(prepare_1_timer_id);

    prepare_2_timer_id = window.setInterval(prepare_2, 250);
    prepare_2();
}

function make_hidden_settings_visible()
{
    if (window.hidden_settings_timer_id) return;
    alert("This option makes hidden device settings visible.\n" +
          "Hidden settings are marked with a '[hidden option]' suffix");

    window.hidden_settings_timer_id = window.setInterval(function() {
        Array.from(document.querySelectorAll('*')).forEach(el => {
            // $(el).hide();
            // $(el).show();
            if($("#ipv4_section").length > 0) {
                $('#ipv4_section .row').css('display', 'block');
            }
            if (el.classList.contains("hide")) {
                el.classList.remove("hide");
                el.innerHTML += "&nbsp;[hidden option]";
            }
        })},
    1000);
}

function have_admin_password_hash()
{
    return cookies.get("admin_password_hash") !== null;
}

function perform_login(successCallback, developer_login = false, save_password_hash = false)
{
    var password_hash = "";

    if (have_admin_password_hash())
        password_hash = cookies.get("admin_password_hash");

    if (password_hash == "")
    {
        var password = prompt("Router Password");

        if (password == null || password == "")
            return;

        password_hash = SHA256(password);
    }

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version,cr_version,RD,LD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD);
            $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data:
                {
                    isTest: "false",
                    goformId: developer_login ? "DEVELOPER_OPTION_LOGIN" : "LOGIN",
                    password: SHA256(password_hash + a.LD),
                    AD: ad
                },
                success: function(a)
                {
                    var j = JSON.parse(a);
                    console.log(j);
                    if ("0" == j.result)
                    {
                        if (save_password_hash) cookies.set("admin_password_hash", password_hash);
                        if (successCallback) successCallback();
                    }
                    else
                    {
                        var reason = "";
                        switch (j.result)
                        {
                            case "1":
                            {
                                reason = "Try again later";
                                break;
                            }
                            case "3":
                            {
                                reason = "Wrong Password";
                                if (have_admin_password_hash())
                                {
                                    console.log("Wrong password. Removing stored password hash ...");
                                    cookies.delete("admin_password_hash");
                                }
                                break;
                            }
                            default: reason = "Unknown";
                        }
                        alert((developer_login ? "Developer login" : "Login") + " failed! Reason: " + reason + ".");
                    }
                },
                error: err
            });
        }
    });
}

function prevent_automatic_logout()
{
    $.ajax({
        type: "GET",
        url: "/tmpl/network/apn_setting.html?v=" + Math.round(+new Date() / 1000)
    });
}

function enable_automatic_login()
{
    var res = confirm("You can make this script log in for you\n" +
                      "once you paste it into the developer console.\n\n" +
                      "The password will be stored in a cookie as an SHA256 hash.\n\n" +
                      "Continue?");

    if (!res)
        return;

    cookies.delete("admin_password_hash");

    perform_login(function() {
        alert("Successfully saved password as hash!");
    }, false, true);
}

function check_log_in(logged_in_callback, not_logged_in_callback = null)
{
    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            // multi_data is required here otherwise
            // a false "ok" might be returned by the
            // router if a session in another browser
            // is running.
            multi_data: "1",
            cmd: "loginfo"
        },
        dataType: "json",
        success: function(a)
        {
            if (a.loginfo.toLowerCase() == "ok")
            {
                if (logged_in_callback)
                    logged_in_callback();
            }
            else
            {
                if (not_logged_in_callback)
                    not_logged_in_callback();
            }
        },
        error: err
    });
}

class LteCaCellInfo
{
    constructor(pci, band, earfcn, bandwidth, rssi, rsrp1, rsrp2, rsrp3, rsrp4, rsrq, sinr1, sinr2, sinr3, sinr4)
    {
        this.pci = pci;
        this.band = band;
        this.earfcn = earfcn;
        this.bandwidth = bandwidth;
        this.rssi = rssi;
        this.rsrp1 = rsrp1;
        this.rsrp2 = rsrp2;
        this.rsrp3 = rsrp3;
        this.rsrp4 = rsrp4;
        this.rsrq = rsrq;
        this.sinr1 = sinr1;
        this.sinr2 = sinr2;
        this.sinr3 = sinr3;
        this.sinr4 = sinr4;
    }
}

function parse_lte_cell_info()
{
    //Object { lte_multi_ca_scell_sig_info: "-44.0,-3.0,19.5,0,2;", lte_multi_ca_scell_info: "1,XX,2,3,1525,15.0" }

    // lte_multi_ca_scell_info
    // 0: CaIndex
    // 1: PCI
    // 2: ??
    // 3: Band
    // 4: Earfcn
    // 5: Bandwidth

    // lte_multi_ca_scell_sig_info
    // 0: RSRP, -44 invalid
    // 1: RSRQ
    // 2: SINR
    // 3: ??
    // 4: ??

    if (!is_lte)
        return [];

    var lte_cells = [];

    var lte_main_band = 
        (lte_ca_pcell_band != "" ? lte_ca_pcell_band : lte_band);

    if (lte_main_band == "")
        lte_main_band = "??";

    lte_cells.push(new LteCaCellInfo(
        parseInt(lte_pci, 16),
        "B" + lte_main_band,
        lte_ca_pcell_freq == "" ? wan_active_channel : lte_ca_pcell_freq,
        (lte_ca_pcell_bandwidth != "" ? lte_ca_pcell_bandwidth : bandwidth).replace("MHz", "").replace(".0", ""),
        lte_rssi,
        lte_rsrp_1,
        lte_rsrp_2,
        lte_rsrp_3,
        lte_rsrp_4,
        lte_rsrq,
        lte_snr_1,
        lte_snr_2,
        lte_snr_3,
        lte_snr_4
    ));

    // lte_multi_ca_scell_sig_info: "rsrp,rsrq,sinr,rssi,?,?;" per SCell (MC888, MC889 B11)

    var scell_infos = lte_multi_ca_scell_info.split(";").filter(n => n);
    var scell_sig_infos = lte_multi_ca_scell_sig_info.split(";").filter(n => n);

    for (var i = 0; i < scell_infos.length; i++)
    {
        if (scell_infos[i] == "")
            continue;

        var scell_info = scell_infos[i].split(",");
        var have_scell_sig_info = scell_sig_infos.length > i;
        var scell_sig_info = have_scell_sig_info ? scell_sig_infos[i].split(",") : undefined;

        if (scell_info.length < 6)
            continue;

        if (have_scell_sig_info && scell_sig_info.length < 3)
            continue;

        lte_cells.push(new LteCaCellInfo(
            scell_info[1], // PCI
            "B" + scell_info[3], // Band
            scell_info[4], // Earfcn
            scell_info[5].replace(".0", ""), // Bandwidth
            "", // RSSI
            (have_scell_sig_info ? (scell_sig_info[0] == "-44.0" ? "?????" : scell_sig_info[0]) : ""), // RSRP
            "",
            "",
            "",
            have_scell_sig_info ? scell_sig_info[1] : "", // RSRQ
            have_scell_sig_info ? scell_sig_info[2] : "", // SINR
            "",
            "",
            ""));
    }

    return lte_cells;
}

class NrCaCellInfo
{
    constructor(pci, band, arfcn, bandwidth, rsrp1, rsrp2, rsrq, rsrp, sinr)
    {
        this.pci = pci;
        this.band = band;
        this.arfcn = arfcn;
        this.bandwidth = bandwidth;
        this.rsrp1 = rsrp1;
        this.rsrp2 = rsrp2;
        this.rsrq = rsrq;
        this.rsrp = rsrp;
        this.sinr = sinr;
        this.unchanged_updates = 0;
        this.info_text = "";
    }
}
  
function parse_nr_cell_info()
{
    if (!is_5g)
        return [];

    if (is_5g_nsa && !is_5g_nsa_active)
    {
        // Base station is capable of 5G NSA
        // but we don't have any receipton of the NSA band.
        return [];
    }

    /*
     * There's apparently no better fix for this.
     * The API does not reset its memory correctly after switching from
     * 5G CA to 5G without CA.
     */ 
    var is_ca = nr_ca_pcell_freq == "" || nr5g_action_channel == nr_ca_pcell_freq;

    if (_5g_rx0_rsrp == "")
        _5g_rx0_rsrp = Z5g_rsrp;

    var nr_cells = [];

    var allowed_nr_bands = 
        (is_5g_nsa ? nr5g_nsa_band_lock : nr5g_sa_band_lock).split(",");

    if (!is_ca) {
        var nr_band =
            (is_5g_nsa ? "n" + nr5g_action_nsa_band : nr5g_action_band);

        if (nr_band == "n" || nr_band == "n-1")
            nr_band = "n??";

        nr_cells.push(new NrCaCellInfo(
            parseInt(nr5g_pci, 16),
            nr_band,
            nr5g_action_channel,
            (is_5g_nsa ? (nr5g_nsa_bandwidth || "") : bandwidth).replace("MHz", ""),
            _5g_rx0_rsrp,
            _5g_rx1_rsrp,
            Z5g_rsrq,
            Z5g_rsrp,
            (Z5g_SINR == "-20.0" || Z5g_SINR == "-3276.8" ? "?????" : Z5g_SINR)
        ));

        previous_nr_cells = nr_cells;
        return nr_cells;
    }

    var pcc_band = nr_ca_pcell_band != ""
    ? nr_ca_pcell_band
    : (nr5g_action_band != ""
        ? (nr5g_action_band[0] == 'n' || nr5g_action_band[0] == 'N'
            ? nr5g_action_band.substr(1)
            : nr5g_action_band)
        : "??");

    var pcc_freq = nr_ca_pcell_freq != ""
        ? nr_ca_pcell_freq
        : (nr5g_action_channel != ""
            ? nr5g_action_channel
            : "??");

    nr_cells.push(new NrCaCellInfo(
        parseInt(nr5g_pci, 16),
        "n" + pcc_band,
        pcc_freq,
        (is_5g_nsa ? (nr5g_nsa_bandwidth || "") : bandwidth).replace("MHz", ""),
        _5g_rx0_rsrp,
        _5g_rx1_rsrp,
        Z5g_rsrq,
        Z5g_rsrp,
        (Z5g_SINR == "-20.0" || Z5g_SINR == "-3276.8" ? "?????" : Z5g_SINR)
    ));

    nr_multi_ca_scell_info.split(";").forEach(cell => {
        if (cell == "")
            return;

        // 0,XX,1,n75,292330,30MHz,0,-73.3,-10.5,17.5;
        // 0  1 2   3      4     5 6     7     8    9
        var cell_data = cell.split(",");

        if (cell_data.length < 10)
            return;

        var nr_band = cell_data[3].replace("n", "");

        /*
         * Try to detect false data. See comment above.
         */
        if (allowed_nr_bands.indexOf(nr_band) == -1)
            return;
    
        nr_cells.push(new NrCaCellInfo(
            cell_data[1], // PCI
            cell_data[3], // Band
            cell_data[4], // Arfcn
            cell_data[5].replace("MHz", ""),
            cell_data[7], // RSRP
            "",
            "",
            cell_data[8], // RSRQ
            (cell_data[9] == "0.0" ? "?????" : cell_data[9]) // SINR
        ));
    });

    /*
     * Try to detect false data. See comment above.
     * Only do this for SCells.
     */
    if (false && typeof previous_nr_cells !== "undefined" && nr_cells.length == previous_nr_cells.length)
    {
        for (var i = 1; i < nr_cells.length; i++)
        {
            if (nr_cells[i].rsrp1 == previous_nr_cells[i].rsrp1 && 
                nr_cells[i].sinr == previous_nr_cells[i].sinr)
            {
                nr_cells[i].unchanged_updates = previous_nr_cells[i].unchanged_updates + 1;
                if (nr_cells[i].unchanged_updates >= 30)
                    nr_cells[i].info_text = "[Data might be invalid]";
            }
        }
    }

    previous_nr_cells = nr_cells;
    return nr_cells;
}

function get_band_info(cells) 
{
    var bands = "";
    cells.forEach(cell => {
        var info = cell.band;
        if (cell.bandwidth != "") info += "(" + cell.bandwidth + "MHz)";
        bands += bands ? " + " : "";
        bands += info;
    });
    return bands;
}

/*
 * Neighbour cells.
 * ngbr_cell_info = "arfcn,pci,rsrq,rsrp,rssi;..." - PCI is DECIMAL here
 * (unlike lte_pci / nr5g_pci which are hex). The router rotates the
 * entries, so we keep everything seen in the last NGBR_KEEP_MS.
 * In ENDC (NSA) the firmware reports LTE neighbours only - no NR
 * neighbour data is exposed by the API (probed ~500 key names, MC889 B11).
 */
var ngbr_seen = {};
var NGBR_KEEP_MS = 60000;

var LTE_BANDS = [[0,599,"B1"],[1200,1949,"B3"],[2750,3449,"B7"],[3450,3799,"B8"],[6150,6449,"B20"],
    [9210,9659,"B28"],[9920,10359,"B32"],[37750,38249,"B38"],[38650,39649,"B40"],[41590,43589,"B42"],[43590,45589,"B43"]];
var NR_BANDS = [[422000,434000,"n1"],[361000,376000,"n3"],[524000,538000,"n7"],[185000,192000,"n8"],
    [158200,160600,"n20/n28"],[160601,164200,"n20"],[151600,158199,"n28"],[514000,523999,"n38"],
    [286400,303400,"n75"],[620000,653333,"n78"]];

function arfcn_to_band(arfcn, nr)
{
    var a = parseInt(arfcn);
    var t = nr ? NR_BANDS : LTE_BANDS;
    for (var i = 0; i < t.length; i++)
        if (a >= t[i][0] && a <= t[i][1]) return t[i][2];
    return "?";
}

var NGBR_COLUMNS = [["rat","RAT"],["band","BAND"],["arfcn","ARFCN"],["pci","PCI"],["rsrp","RSRP","dBm, latest"],["avg","AVG","dBm, RSRP mean of last 60 s"],
    ["range","MIN/MAX","dBm, RSRP over last 60 s (hover: samples)"],["rsrq","RSRQ","dB"],["sinr","SINR","dB"],["rssi","RSSI","dBm"],["dist","DIST","serving cell timing advance"],
    ["enb","CELL","eNB-sector learned while connected to this cell"],["note","NOTE"]];
var ngbr_sort = { key: "band", dir: 1 };

function ngbr_sort_value(c, key)
{
    switch (key)
    {
        case "rat":  return c.nr ? 1 : 0;
        case "band": return (c.nr ? 1000 : 0) + (parseInt(c.band.replace(/^\D+/, "")) || 999);  // B1 < B3 < B7 < B20 < n1 ...
        case "note": return c.mark == "SERVING" ? 0 : (c.mark == "CA" ? 1 : (c.better ? 2 : 3));
        case "dist": return c.dist === "" ? 99999 : c.dist;
        case "range": return c.n ? c.max - c.min : 99999;           // most stable first
        case "enb":  return c.eci === undefined ? 1e12 : c.eci;
        case "avg":  return isNaN(c.avg) ? -9999 : c.avg;
        default:     return parseFloat(c[key]) || -9999;  // arfcn, pci, rsrp, rsrq, rssi
    }
}

// click same header again to reverse; signal columns start strongest-first
function ngbr_sort_by(key)
{
    ngbr_sort = { key: key, dir: ngbr_sort.key == key ? -ngbr_sort.dir : (["rsrp", "avg", "rsrq", "sinr", "rssi"].includes(key) ? -1 : 1) };
    $("#ngbr_cell_info").html(render_ngbr_cells(null));
}

// lock straight from a NGBR row; SCS: 30 kHz for TDD mid-band (n38/n41/n77/n78/n79), else 15 kHz
function ngbr_lock(nr, arfcn, pci, band)
{
    if (!nr) return lte_cell_lock(false, pci + "," + arfcn);
    var b = band.replace(/^n/, "");
    var known = /^\d+$/.test(b);   // "n20/n28" overlap or "?" -> let the user fix it in the prompt
    var scs = ["38", "41", "77", "78", "79"].includes(b) ? "30" : "15";
    nr_cell_lock(false, pci + "," + arfcn + "," + (known ? b : "") + "," + scs, !known);
}

function sinr_or_unknown(v, scell = false)
{
    return (v == "-20.0" || v == "-3276.8" || (scell && v == "0.0")) ? "?" : (v || "");
}

// PCell + LTE SCells (lte_multi_ca_scell_info/_sig_info) + NR PCell/SCells, same order as the signal panels
function connected_cells()
{
    var cells = [];
    if (is_lte && wan_active_channel)
        cells.push({ nr: false, arfcn: lte_ca_pcell_freq || wan_active_channel, pci: String(parseInt(lte_pci, 16)),
                     rsrp: lte_rsrp, rsrq: lte_rsrq, sinr: lte_snr, rssi: lte_rssi });

    var sig = (lte_multi_ca_scell_sig_info || "").split(";").filter(Boolean);
    (lte_multi_ca_scell_info || "").split(";").filter(Boolean).forEach(function(info, i) {
        var d = info.split(","), g = (sig[i] || "").split(",");  // d: idx,pci,?,band,earfcn,bw  g: rsrp,rsrq,sinr,rssi
        if (d.length < 5) return;
        cells.push({ nr: false, arfcn: d[4], pci: d[1], rsrp: g[0] == "-44.0" ? "" : (g[0] || ""), rsrq: g[1] || "",
                     sinr: g[2] || "", rssi: g[3] || "" });
    });

    if (is_5g && (!is_5g_nsa || is_5g_nsa_active) && nr5g_action_channel && Z5g_rsrp)
        cells.push({ nr: true, arfcn: nr5g_action_channel, pci: String(parseInt(nr5g_pci, 16)),
                     rsrp: Z5g_rsrp, rsrq: Z5g_rsrq, sinr: sinr_or_unknown(Z5g_SINR), rssi: Z5g_rssi || "" });

    (nr_multi_ca_scell_info || "").split(";").filter(Boolean).forEach(function(info) {
        var d = info.split(",");  // 0,PCI,1,n75,ARFCN,BW,0,RSRP,RSRQ,SINR
        if (d.length < 10) return;
        cells.push({ nr: true, arfcn: d[4], pci: d[1], rsrp: d[7], rsrq: d[8], sinr: sinr_or_unknown(d[9], true), rssi: "" });
    });
    return cells;
}

var ngbr_hist = {};                                   // key -> [{t, v}] RSRP samples, last NGBR_KEEP_MS
var cell_ids = tools_store("zte_cell_ids", {});
var cell_id_streak = {};       // "lte:earfcn:pci" -> ECI (decimal), learned while connected

// raw === null: re-render only (sort click) - no new samples
function render_ngbr_cells(raw)
{
    var now = Date.now();
    var refresh = raw !== null;
    (raw || "").split(";").forEach(function(cell) {
        var p = cell.split(",").map(function(x) { return x.trim(); });
        if (p.length < 4 || p[0] === "") return;
        // ponytail: NR = ARFCN above LTE range; never seen in NSA, kept in case firmware adds it
        var nr = parseInt(p[0]) > 65535;
        ngbr_seen[(nr ? "nr" : "lte") + ":" + p[0] + ":" + p[1]] = {
            nr: nr, arfcn: p[0], pci: p[1], rsrq: p[2], rsrp: p[3],
            rssi: nr ? "" : (p[4] || ""), sinr: "", extra: nr ? p.slice(4).join(",") : p.slice(5).join(","),
            raw: cell, t: now
        };
    });

    // connected cells: neighbour list has no SINR, so overwrite them with the serving/CA measurements
    if (refresh) connected_cells().forEach(function(c) {
        c.t = now;
        c.extra = "";
        c.raw = "connected: " + [c.arfcn, c.pci, c.rsrq, c.rsrp, c.sinr, c.rssi].join(",");
        ngbr_seen[(c.nr ? "nr" : "lte") + ":" + c.arfcn + ":" + c.pci] = c;
    });

    var ca = {};
    (lte_multi_ca_scell_info || "").split(";").forEach(function(c) {
        var d = c.split(",");
        if (d.length >= 5) ca[d[4] + ":" + d[1]] = true;
    });
    var nr_ca = {};
    (nr_multi_ca_scell_info || "").split(";").forEach(function(c) {
        var d = c.split(",");
        if (d.length >= 5) nr_ca[d[4] + ":" + d[1]] = true;
    });
    var lte_serving = (lte_ca_pcell_freq || wan_active_channel) + ":" + parseInt(lte_pci, 16);
    var nr_serving = nr5g_action_channel + ":" + parseInt(nr5g_pci, 16);

    // learn cell id of the cell we are actually connected to (get_status already made cell_id decimal)
    if (refresh)
    {
        var learned = false;
        [["lte:" + lte_serving, is_lte ? cell_id : ""], ["nr:" + nr_serving, is_5g ? nr5g_cell_id : ""]].forEach(function(x) {
            var id = parseInt(x[1]);
            if (isNaN(id)) return;
            var st = cell_id_streak[x[0]] = (cell_id_streak[x[0]] && cell_id_streak[x[0]].id == id) ? { id: id, n: cell_id_streak[x[0]].n + 1 } : { id: id, n: 1 };
            if (st.n >= 3 && cell_ids[x[0]] != id) { cell_ids[x[0]] = id; learned = true; }
        });
        if (learned) tools_save("zte_cell_ids", cell_ids);
    }

    // 1 TA step = 16 Ts = 78.12 m one-way; parseInt also works once get_status has formatted lte_ta as "2 (~156 m)"
    var ta = is_lte ? parseInt(lte_ta) : NaN;
    if (isNaN(ta)) ta = -1;

    var cells = [];
    for (var k in ngbr_seen)
    {
        var c = ngbr_seen[k];
        if (now - c.t > NGBR_KEEP_MS) { delete ngbr_seen[k]; delete ngbr_hist[k]; continue; }
        var key = c.arfcn + ":" + c.pci;
        c.mark = c.nr ? (key == nr_serving ? "SERVING" : (nr_ca[key] ? "CA" : ""))
                      : (key == lte_serving ? "SERVING" : (ca[key] ? "CA" : ""));
        c.band = arfcn_to_band(c.arfcn, c.nr);
        c.dist = (!c.nr && ta >= 0 && key == lte_serving) ? Math.round(ta * 78.12) : "";
        c.eci = cell_ids[k];

        // per-cell RSRP statistics over the last minute (only fresh readings are sampled)
        var h = ngbr_hist[k] = ngbr_hist[k] || [];
        var v = parseFloat(c.rsrp);
        if (c.t == now && !isNaN(v)) h.push({ t: now, v: v });
        while (h.length && now - h[0].t > NGBR_KEEP_MS) h.shift();
        c.n = h.length;
        c.avg = c.n ? h.reduce(function(s, x) { return s + x.v; }, 0) / c.n : NaN;
        c.min = c.n ? Math.min.apply(null, h.map(function(x) { return x.v; })) : NaN;
        c.max = c.n ? Math.max.apply(null, h.map(function(x) { return x.v; })) : NaN;
        cells.push(c);
    }

    // "better cell" hint: a neighbour on the same carrier whose 60 s mean RSRP beats the connected cell by >= 3 dB
    var connected_avg = {};
    cells.forEach(function(c) { if (c.mark && !isNaN(c.avg)) connected_avg[(c.nr ? "nr:" : "lte:") + c.arfcn] = c.avg; });
    cells.forEach(function(c) {
        var ref = connected_avg[(c.nr ? "nr:" : "lte:") + c.arfcn];
        c.better = (!c.mark && ref !== undefined && c.n >= 3 && c.avg - ref >= 3) ? c.avg - ref : 0;
    });

    // clicked column first, then RAT / ARFCN / strongest RSRP as tie-breakers
    cells.sort(function(a, b) {
        var va = ngbr_sort_value(a, ngbr_sort.key), vb = ngbr_sort_value(b, ngbr_sort.key);
        var d = typeof va == "string" ? va.localeCompare(vb) : va - vb;
        return (d * ngbr_sort.dir) || (a.nr - b.nr) || (parseInt(a.arfcn) - parseInt(b.arfcn)) || (parseFloat(b.rsrp) - parseFloat(a.rsrp));
    });

    var html = "<table class='ngbr_cell_table'><tr>";
    NGBR_COLUMNS.forEach(function(col) {
        var arrow = ngbr_sort.key == col[0] ? (ngbr_sort.dir > 0 ? "&nbsp;&#9650;" : "&nbsp;&#9660;") : "";
        html += "<th style='cursor:pointer'" + (col[2] ? " title='" + col[2] + "'" : "") +
                " onclick=\"ngbr_sort_by('" + col[0] + "')\">" + col[1] + arrow + "</th>";
    });
    html += "<th>LOCK</th></tr>";
    cells.forEach(function(c) {
        var age = Math.round((now - c.t) / 1000);
        var cellid = c.eci === undefined ? "" : (c.nr ? String(c.eci) : Math.floor(c.eci / 256) + "-" + (c.eci % 256));
        html += "<tr title='" + c.raw + "' style='opacity:" + (age > 3 ? 0.5 : 1) + "'>" +
            "<td>" + (c.nr ? "NR" : "LTE") + "</td><td>" + c.band + "</td>" +
            "<td>" + c.arfcn + "</td><td>" + c.pci + "</td>" +
            "<td>" + c.rsrp + "</td>" +
            "<td>" + (c.n ? c.avg.toFixed(1) : "") + "</td>" +
            "<td title='" + c.n + " samples'>" + (c.n ? c.min + "/" + c.max : "") + "</td>" +
            "<td>" + c.rsrq + "</td><td>" + c.sinr + "</td>" +
            "<td>" + (c.rssi || c.extra) + "</td>" +
            "<td" + (c.dist === "" ? ">" : " title='Serving cell timing advance (TA " + ta + ", &plusmn;39 m)'>~" + c.dist + "&nbsp;m") + "</td>" +
            "<td" + (c.eci === undefined ? "" : " title='ECI " + c.eci + "'") + ">" + cellid + "</td>" +
            "<td><b>" + c.mark + "</b>" +
                (c.better ? "<b style='color:#5c5'>BETTER +" + c.better.toFixed(1) + "</b>" : "") +
                (age > 3 ? " " + age + "s" : "") + "</td>" +
            "<td><a style='cursor:pointer' title='Lock to this cell (reboot required)' onclick=\"ngbr_lock(" +
                c.nr + ",'" + c.arfcn + "','" + c.pci + "','" + c.band + "')\">lock</a></td></tr>";
    });
    return html + "</table>";
}

/*
 * IMSI and router GPS position are AES-GCM encrypted by the API with a
 * per-session key; the stock service module decrypts them for us.
 */
function load_device_extras()
{
    if (typeof require !== "function") return;
    require(["service"], function(service) {
        try {
            var di = service.getDeviceInfo();
            var html = "IMEI: " + di.imei + "&nbsp;&nbsp;IMSI: " + di.imsi;
            if (di.gps_lat && di.gps_lon)
                html += "&nbsp;&nbsp;GPS: <a target='_blank' href='https://www.openstreetmap.org/?mlat=" +
                    di.gps_lat + "&mlon=" + di.gps_lon + "#map=16/" + di.gps_lat + "/" + di.gps_lon + "'>" +
                    di.gps_lat + ", " + di.gps_lon + "</a>";
            $("#device_extras").html(html);
            $("#device_extras_row").show();
        } catch (e) { console.log("device extras unavailable", e); }
    });
}

/*
 * ===== Alignment & cell tools =====
 * Uses only values the router reports. Positions, learned cell ids and
 * experiment results live in this browser's localStorage.
 */
var TOOLS_HISTORY_MS = 5 * 60 * 1000;    // sparkline window
var TOOLS_AVG_MS = 3000;                 // smoothing window for "now" (router values jump)
var ALIGN_CMD = "Z5g_rsrp,Z5g_rsrq,Z5g_SINR,5g_rx0_rsrp,5g_rx1_rsrp,lte_rsrp,lte_rsrq,lte_snr,lte_rsrp_1,lte_rsrp_2,lte_rsrp_3,lte_rsrp_4," +
                "network_type,wan_active_channel,lte_ca_pcell_freq,lte_pci,nr5g_action_channel,nr5g_pci,realtime_rx_thrpt,realtime_tx_thrpt";
// [key, router field, label, unit]
var ALIGN_METRICS = [
    ["nr_sinr", "Z5g_SINR", "5G SINR", "dB"], ["nr_rsrp", "Z5g_rsrp", "5G RSRP", "dBm"], ["nr_rsrq", "Z5g_rsrq", "5G RSRQ", "dB"],
    ["nr_rx0", "5g_rx0_rsrp", "5G RSRP rx0", "dBm"], ["nr_rx1", "5g_rx1_rsrp", "5G RSRP rx1", "dBm"],
    ["lte_sinr", "lte_snr", "LTE SINR", "dB"], ["lte_rsrp", "lte_rsrp", "LTE RSRP", "dBm"], ["lte_rsrq", "lte_rsrq", "LTE RSRQ", "dB"],
    ["lte_p1", "lte_rsrp_1", "LTE RSRP port 1", "dBm"], ["lte_p2", "lte_rsrp_2", "LTE RSRP port 2", "dBm"],
    ["lte_p3", "lte_rsrp_3", "LTE RSRP port 3", "dBm"], ["lte_p4", "lte_rsrp_4", "LTE RSRP port 4", "dBm"]
];
var tools = { hist: {}, base: {}, peak: {}, fast_timer: null, tone: null, tone_mode: "dual", tone_res: "high" };

function tools_store(name, def) { try { var v = JSON.parse(localStorage.getItem(name)); return v === null ? def : v; } catch (e) { return def; } }
function tools_save(name, v) { try { localStorage.setItem(name, JSON.stringify(v)); } catch (e) { console.log("localStorage unavailable", e); } }

function tools_num(field, v)
{
    if (v === undefined || v === "") return NaN;
    if (field == "Z5g_SINR" && (v == "-20.0" || v == "-3276.8")) return NaN;  // router's "no value" markers
    return parseFloat(v);
}

function tools_avg(key, ms)
{
    var h = tools.hist[key] || [], now = Date.now(), s = 0, n = 0;
    for (var i = h.length - 1; i >= 0 && now - h[i].t <= (ms || TOOLS_AVG_MS); i--) { s += h[i].v; n++; }
    return n ? s / n : NaN;
}

// fed by get_status (1 s) and by the fast alignment poll (0.5 s)
function tools_feed(a)
{
    var now = Date.now();
    ALIGN_METRICS.forEach(function(m) {
        var v = tools_num(m[1], a[m[1]]);
        if (isNaN(v)) return;
        var h = tools.hist[m[0]] = tools.hist[m[0]] || [];
        h.push({ t: now, v: v });
        while (h.length && now - h[0].t > TOOLS_HISTORY_MS) h.shift();
        var avg = tools_avg(m[0]);
        if (tools.base[m[0]] === undefined) tools.base[m[0]] = avg;
        if (tools.peak[m[0]] === undefined || avg > tools.peak[m[0]].v) tools.peak[m[0]] = { v: avg, t: now };
    });
    exp_tick(a, now);
    tools_render();
    tone_update();
}

function sparkline(key)
{
    var h = tools.hist[key] || [];
    if (h.length < 2) return "";
    // x axis grows with the data until the 5 min window is full
    var w = 160, ht = 24, now = Date.now(), t0 = Math.max(h[0].t, now - TOOLS_HISTORY_MS), tspan = (now - t0) || 1;
    var vs = h.map(function(p) { return p.v; }), lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs), span = (hi - lo) || 1;
    var pts = h.map(function(p) {
        return ((p.t - t0) / tspan * w).toFixed(1) + "," + (hi == lo ? ht / 2 : ht - 2 - (p.v - lo) / span * (ht - 4)).toFixed(1);
    }).join(" ");
    return "<svg width='" + w + "' height='" + ht + "'><title>" + lo + " .. " + hi + "</title>" +
           "<polyline fill='none' stroke='#40adf5' stroke-width='1.5' points='" + pts + "'/></svg>";
}

function branch_warning()
{
    var w = [];
    var r0 = tools_avg("nr_rx0"), r1 = tools_avg("nr_rx1");
    if (!isNaN(r0) && !isNaN(r1) && Math.abs(r0 - r1) > 6) w.push("5G rx0/rx1 differ by " + Math.abs(r0 - r1).toFixed(1) + " dB");
    var p = ["lte_p1", "lte_p2", "lte_p3", "lte_p4"].map(function(k) { return tools_avg(k); }).filter(function(v) { return !isNaN(v); });
    if (p.length > 1 && Math.max.apply(null, p) - Math.min.apply(null, p) > 6)
        w.push("LTE ports differ by " + (Math.max.apply(null, p) - Math.min.apply(null, p)).toFixed(1) + " dB");
    return w.length ? "<b style='color:#f90'>" + w.join("; ") + "</b> - try rotating / repositioning" : "balanced (&le; 6 dB)";
}

function tools_render()
{
    $(".branch_warn").html(branch_warning());
    if (!$("#align_table").length) return;
    var html = "<tr><th>METRIC</th><th>NOW (3 s)</th><th>&Delta; START</th><th>PEAK</th><th>LAST 5 MIN</th></tr>";
    ALIGN_METRICS.forEach(function(m) {
        var now = tools_avg(m[0]);
        if (isNaN(now)) return;
        var d = now - tools.base[m[0]], pk = tools.peak[m[0]];
        html += "<tr><td>" + m[2] + "</td><td><b>" + now.toFixed(1) + "</b>&nbsp;" + m[3] + "</td>" +
                "<td style='color:" + (d >= 0 ? "#5c5" : "#e66") + "'>" + (d >= 0 ? "+" : "") + d.toFixed(1) + "</td>" +
                "<td title='" + new Date(pk.t).toLocaleTimeString() + "'>" + pk.v.toFixed(1) + "</td>" +
                "<td>" + sparkline(m[0]) + "</td></tr>";
    });
    $("#align_table").html(html);
}

function tools_reset() { tools.base = {}; tools.peak = {}; if (tools.tone) tools.tone.peaks = {}; }

function align_fast(on)
{
    window.clearInterval(tools.fast_timer);
    tools.fast_timer = on ? window.setInterval(function() {
        $.getJSON("/goform/goform_get_cmd_process", { cmd: ALIGN_CMD, multi_data: "1" }, tools_feed);
    }, 500) : null;
}

/*
 * Alignment sounds on the computer speakers (off by default). Modes (tools.tone_mode):
 *  "dual"     5G + LTE SINR: each beep is a pair - high note = 5G, low note = LTE, pitch rises with each SINR;
 *             beeps speed up as the weaker of the two improves.
 *             chirps: 5G new best = two rising high notes, LTE new best = two falling notes,
 *             both at their best at the same time = three-note rising chime.
 *  "balance"  5G rx0/rx1: steady tone when within 2 dB; otherwise beeps that get faster as the
 *             difference shrinks - LOW beeps = rx0 stronger, HIGH beeps = rx1 stronger.
 *  "m:<key>"  one metric: faster + higher beeps for better values, double chirp on a new best.
 * A "new best" is +0.5 dB over the best 3 s average since the tone / reset / mode change.
 */
var TONE_LEGEND = {
    dual: "pair of beeps: high = 5G SINR, low = LTE SINR (pitch rises with SINR, faster when the weaker improves). " +
          "Chirp up = 5G best, chirp down = LTE best, 3-note chime = both at best.",
    balance: "steady tone = rx0/rx1 balanced (2 / 1 / 0.5 dB by resolution). Low beeps = rx0 stronger, high beeps = rx1 stronger; faster = closer to balanced.",
    metric: "faster and higher = better; double chirp = new best."
};

function tone_toggle(on)
{
    if (tools.tone) { window.clearTimeout(tools.tone.timer); tools.tone.ctx.close(); tools.tone = null; }
    if (!on) return;
    tools.tone = { ctx: new (window.AudioContext || window.webkitAudioContext)(), timer: null, peaks: {}, hold: null, chime_at: 0, chirp_at: 0 };
    tone_loop();
}

function tone_set_mode(mode)
{
    tools.tone_mode = mode;
    if (tools.tone) { tools.tone.peaks = {}; tone_hold(0); }
    $("#tone_legend").html(TONE_LEGEND[mode.indexOf("m:") == 0 ? "metric" : mode]);
}

function tone_beep(freq, start, len)
{
    var ctx = tools.tone.ctx, osc = ctx.createOscillator(), g = ctx.createGain();
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(0.08, start + 0.005);   // short ramps: no clicks
    g.gain.exponentialRampToValueAtTime(0.0001, start + len);
    osc.connect(g);
    g.connect(ctx.destination);
    osc.start(start);
    osc.stop(start + len + 0.02);
}

// sustained tone for "balanced"; freq 0 = off
function tone_hold(freq)
{
    var t = tools.tone, now = t.ctx.currentTime;
    if (!freq) { if (t.hold) { t.hold.g.gain.setTargetAtTime(0.0001, now, 0.03); t.hold.osc.stop(now + 0.2); t.hold = null; } return; }
    if (t.hold) return;
    var osc = t.ctx.createOscillator(), g = t.ctx.createGain();
    osc.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, now);
    g.gain.setTargetAtTime(0.05, now, 0.03);
    osc.connect(g);
    g.connect(t.ctx.destination);
    osc.start(now);
    t.hold = { osc: osc, g: g };
}

function tone_chord(freqs)
{
    var t = tools.tone.ctx.currentTime;
    freqs.forEach(function(f, i) { tone_beep(f, t + i * 0.08, 0.06); });
}

/*
 * Resolution. "abs": pitch spread over a fixed range (SINR -10..30 dB etc.) - small changes are hard to hear.
 * "high"/"vhigh": pitch is relative to the reference (value at tone start / Reset start/peak):
 * every dB of change = 2 (high) or 4 (very high) semitones, so even 0.3 dB is audible on a weak signal.
 * k: semitones per dB, thr: balance window (dB), best: dB needed for a "new best" chirp.
 */
var TONE_RES = { abs: { k: 0, thr: 2, best: 0.5 }, high: { k: 2, thr: 1, best: 0.3 }, vhigh: { k: 4, thr: 0.5, best: 0.2 } };
var TONE_AVG_MS = 1500;   // shorter than the display average: the tone should react quickly (use Fast update)

function tone_set_res(res)
{
    tools.tone_res = res;
    if (tools.tone) tools.tone.peaks = {};
}

// metric mapped to 0..1 over a typical range (SINR -10..30 dB, RSRP -125..-60 dBm, RSRQ -20..-3 dB)
function tone_level(key)
{
    var m = ALIGN_METRICS.filter(function(x) { return x[0] == key; })[0], v = tools_avg(key, TONE_AVG_MS);
    if (!m || isNaN(v)) return NaN;
    var r = m[3] == "dBm" ? [-125, -60] : (m[0].indexOf("rsrq") >= 0 ? [-20, -3] : [-10, 30]);
    return Math.min(1, Math.max(0, (v - r[0]) / (r[1] - r[0])));
}

// one voice: frequency between lo..hi Hz and a 0..1 "goodness" that drives the beep speed
function tone_voice(key, lo, hi)
{
    var v = tools_avg(key, TONE_AVG_MS), r = TONE_RES[tools.tone_res];
    if (isNaN(v)) return null;
    if (!r.k) { var x = tone_level(key); return { f: lo + x * (hi - lo), x: x }; }
    var ref = tools.base[key] === undefined ? v : tools.base[key];
    var semis = Math.max(-24, Math.min(24, (v - ref) * r.k));              // +-2 octaves around the middle pitch
    return { f: Math.sqrt(lo * hi) * Math.pow(2, semis / 12), x: 0.5 + semis / 48 };
}

function tone_loop()
{
    var t = tools.tone;
    if (!t) return;
    var now = t.ctx.currentTime, next = 500, mode = tools.tone_mode, r = TONE_RES[tools.tone_res];

    if (mode == "balance")
    {
        var d = tools_avg("nr_rx0", TONE_AVG_MS) - tools_avg("nr_rx1", TONE_AVG_MS);
        if (isNaN(d)) tone_hold(0);
        else if (Math.abs(d) <= r.thr) { tone_hold(660); next = 200; }
        else
        {
            tone_hold(0);
            tone_beep(d > 0 ? 440 : 990, now, 0.07);
            // closer to balance -> faster; finer resolutions speed up over a smaller dB range
            next = Math.min(1200, 120 + (Math.abs(d) - r.thr) * (r.k ? 400 / r.k : 100));
        }
    }
    else if (mode == "dual")
    {
        tone_hold(0);
        var v5 = tone_voice("nr_sinr", 700, 1600), v4 = tone_voice("lte_sinr", 250, 650);
        if (v5) tone_beep(v5.f, now, 0.06);            // 5G: high voice
        if (v4) tone_beep(v4.f, now + 0.09, 0.06);     // LTE: low voice
        if (v5 || v4) next = Math.max(250, 1000 - Math.min(v5 ? v5.x : 1, v4 ? v4.x : 1) * 750);
    }
    else
    {
        tone_hold(0);
        var v = tone_voice(mode.substr(2), 400, 1200);
        if (v) { tone_beep(v.f, now, 0.06); next = 1000 - v.x * 880; }   // 1 .. ~8 beeps/s
    }
    t.timer = window.setTimeout(tone_loop, next);
}

// called on every new reading: track bests and play the "new best" sounds
function tone_update()
{
    var t = tools.tone;
    if (!t || tools.tone_mode == "balance") return;
    var keys = tools.tone_mode == "dual" ? ["nr_sinr", "lte_sinr"] : [tools.tone_mode.substr(2)];
    var improved = {};
    keys.forEach(function(k) {
        var v = tools_avg(k, TONE_AVG_MS);
        if (isNaN(v)) return;
        if (t.peaks[k] !== undefined && v >= t.peaks[k] + TONE_RES[tools.tone_res].best) improved[k] = true;
        if (t.peaks[k] === undefined || v > t.peaks[k]) t.peaks[k] = v;
    });
    if (!Object.keys(improved).length || Date.now() - t.chirp_at < 1000) return;   // at most one chirp per second
    t.chirp_at = Date.now();

    if (tools.tone_mode != "dual") { tone_chord([1800, 2400]); return; }
    var at_best = function(k) { var v = tools_avg(k, TONE_AVG_MS); return !isNaN(v) && t.peaks[k] !== undefined && v >= t.peaks[k] - TONE_RES[tools.tone_res].best; };
    if (at_best("nr_sinr") && at_best("lte_sinr") && Date.now() - t.chime_at > 3000)
    {
        t.chime_at = Date.now();
        tone_chord([1200, 1600, 2000]);           // both 5G and LTE at their best
    }
    else if (improved.nr_sinr) tone_chord([1800, 2400]);   // 5G new best: rising
    else tone_chord([1600, 1200]);                         // LTE new best: falling
}

/* ---- lock / band status ---- */
var LTE_ALL_BANDS = "0xA3E2AB0908DF";
function lte_mask_to_bands(mask)
{
    if (!mask || !/^0x[0-9a-f]+$/i.test(mask)) return "";
    var n = BigInt(mask), out = [];
    if (n == BigInt(LTE_ALL_BANDS)) return "all supported";
    for (var b = 1; b <= 64; b++) if ((n >> BigInt(b - 1)) & BigInt(1)) out.push("B" + b);
    return out.join(" ");
}

function lock_status_html()
{
    var parts = [];
    var lte_locked = lte_pci_lock && lte_pci_lock != "0" && lte_earfcn_lock && lte_earfcn_lock != "0";
    parts.push("LTE cell: " + (lte_locked ? "<b style='color:#f90'>PCI " + lte_pci_lock + " @ " + lte_earfcn_lock + "</b>" : "none"));
    var nl = (nr5g_cell_lock || "").split(",");
    var nr_locked = nl.length >= 2 && nl[0] !== "" && nl[0] != "0";
    parts.push("5G cell: " + (nr_locked ? "<b style='color:#f90'>PCI " + nl[0] + " @ " + nl[1] +
               (nl[2] ? " n" + nl[2] : "") + (nl[3] ? " SCS " + nl[3] : "") + "</b>" : "none"));
    var lb = lte_mask_to_bands(lte_band_lock);
    if (lb) parts.push("LTE bands: " + lb);
    var nb = is_5g_nsa ? nr5g_nsa_band_lock : nr5g_sa_band_lock;
    if (nb) parts.push("5G " + (is_5g_nsa ? "NSA" : "SA") + " bands: " + nb.split(",").map(function(x) { return "n" + x; }).join(" "));
    return parts.join("<br>");
}

/* ---- saved positions ---- */
var POSITION_COLS = ["nr_sinr", "nr_rsrp", "nr_rsrq", "nr_rx0", "nr_rx1", "lte_sinr", "lte_rsrp", "lte_rsrq"];

function position_save()
{
    var list = tools_store("zte_positions", []);
    var label = prompt("Label for this antenna position (e.g. window left, +30 deg)", "#" + (list.length + 1));
    if (label === null) return;
    var rec = { time: new Date().toLocaleString(), label: label };
    ALIGN_METRICS.forEach(function(m) { var v = tools_avg(m[0], 5000); if (!isNaN(v)) rec[m[0]] = +v.toFixed(1); });
    list.push(rec);
    tools_save("zte_positions", list);
    positions_render();
}

function records_table(list, cols, del_fn)
{
    // highlight the best value per column, only when the column actually differs
    var best = {};
    cols.forEach(function(c) {
        var vs = list.map(function(r) { return r[c]; }).filter(function(v) { return typeof v == "number"; });
        if (vs.length > 1 && Math.max.apply(null, vs) != Math.min.apply(null, vs)) best[c] = Math.max.apply(null, vs);
    });
    var html = "<tr><th>TIME</th><th>LABEL</th>" + cols.map(function(c) {
        var m = ALIGN_METRICS.filter(function(x) { return x[0] == c; })[0];
        return "<th>" + (m ? m[2].replace("RSRP ", "") : c.toUpperCase()) + "</th>";
    }).join("") + "<th></th></tr>";
    list.forEach(function(r, i) {
        html += "<tr><td>" + r.time + "</td><td>" + r.label + "</td>" + cols.map(function(c) {
            var v = r[c] === undefined ? "" : r[c];
            return "<td>" + (v !== "" && v === best[c] ? "<b style='color:#5c5'>" + v + "</b>" : v) + "</td>";
        }).join("") + "<td><a style='cursor:pointer' onclick='" + del_fn + "(" + i + ")'>x</a></td></tr>";
    });
    return html;
}

function positions_render() { $("#positions_table").html(records_table(tools_store("zte_positions", []), POSITION_COLS, "position_delete")); }
function position_delete(i) { var l = tools_store("zte_positions", []); l.splice(i, 1); tools_save("zte_positions", l); positions_render(); }

function csv_export(name, filename)
{
    var list = tools_store(name, []);
    if (!list.length) { alert("Nothing to export."); return; }
    var cols = [];
    list.forEach(function(r) { Object.keys(r).forEach(function(k) { if (cols.indexOf(k) < 0) cols.push(k); }); });
    var csv = [cols.join(",")].concat(list.map(function(r) {
        return cols.map(function(c) { var v = r[c] === undefined ? "" : String(r[c]); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(",");
    })).join("\n");
    var a = document.createElement("a");
    a.href = "data:text/csv;charset=utf-8," + encodeURIComponent(csv);
    a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
}

function records_clear(name, render) { if (confirm("Delete all saved entries?")) { tools_save(name, []); render(); } }

/* ---- lock experiments / cell evaluation: wait until camped on target, then average 60 s ---- */
var EXP_COLS = ["lte_rsrp", "lte_sinr", "lte_rsrq", "nr_rsrp", "nr_sinr", "nr_rsrq", "rx_mbit", "tx_mbit"];
var EXP_FIELDS = [["lte_rsrp", "lte_rsrp"], ["lte_sinr", "lte_snr"], ["lte_rsrq", "lte_rsrq"], ["nr_rsrp", "Z5g_rsrp"],
                  ["nr_sinr", "Z5g_SINR"], ["nr_rsrq", "Z5g_rsrq"], ["rx_mbit", "realtime_rx_thrpt"], ["tx_mbit", "realtime_tx_thrpt"]];
var exp_run = null;

function exp_serving(a, nr)
{
    return nr ? a.nr5g_action_channel + ":" + parseInt(a.nr5g_pci, 16)
              : (a.lte_ca_pcell_freq || a.wan_active_channel) + ":" + parseInt(a.lte_pci, 16);
}

function exp_arm(nr, arfcn, pci, label)
{
    tools_save("zte_exp_pending", { nr: nr, target: arfcn + ":" + pci, label: label, armed: Date.now() });
    exp_run = null;
}

function exp_measure_now()
{
    var label = prompt("Label for this measurement", "LTE PCI " + parseInt(lte_pci, 16) + " @ " + (lte_ca_pcell_freq || wan_active_channel));
    if (label === null) return;
    exp_arm(false, lte_ca_pcell_freq || wan_active_channel, parseInt(lte_pci, 16), label);
}

function exp_tick(a, now)
{
    if (a.network_type === undefined) return;
    var p = tools_store("zte_exp_pending", null);
    if (!p) return;
    if (!exp_run)
    {
        if (exp_serving(a, p.nr) == p.target) exp_run = { start: now, sums: {}, ns: {} };
        else if (now - p.armed > 10 * 60 * 1000) exp_finish(p, { result: "not camped on target within 10 min" });
        else $("#exp_state").html("waiting for " + p.target + " ...");
        return;
    }
    if (exp_serving(a, p.nr) != p.target) { exp_finish(p, { result: "left target cell during measurement" }); return; }
    EXP_FIELDS.forEach(function(f) {
        var v = tools_num(f[1], a[f[1]]);
        if (isNaN(v)) return;
        if (f[0].indexOf("mbit") > 0) v = v * 8 / 1e6;
        exp_run.sums[f[0]] = (exp_run.sums[f[0]] || 0) + v;
        exp_run.ns[f[0]] = (exp_run.ns[f[0]] || 0) + 1;
    });
    var secs = Math.round((now - exp_run.start) / 1000);
    $("#exp_state").html("measuring " + p.label + ": " + secs + "/60 s");
    if (secs >= 60)
    {
        var rec = { result: "ok" };
        EXP_FIELDS.forEach(function(f) { if (exp_run.ns[f[0]]) rec[f[0]] = +(exp_run.sums[f[0]] / exp_run.ns[f[0]]).toFixed(f[0].indexOf("mbit") > 0 ? 2 : 1); });
        exp_finish(p, rec);
    }
}

function exp_finish(p, rec)
{
    rec.time = new Date().toLocaleString();
    rec.label = p.label + " (" + p.target + ")";
    var list = tools_store("zte_exp_results", []);
    list.push(rec);
    tools_save("zte_exp_results", list);
    try { localStorage.removeItem("zte_exp_pending"); } catch (e) {}
    exp_run = null;
    $("#exp_state").html("last: " + rec.label + " - " + rec.result);
    exp_render();
}

function exp_render() { $("#exp_table").html(records_table(tools_store("zte_exp_results", []), EXP_COLS.concat(["result"]), "exp_delete")); }
function exp_delete(i) { var l = tools_store("zte_exp_results", []); l.splice(i, 1); tools_save("zte_exp_results", l); exp_render(); }

function get_status()
{
    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: siginfo,
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            for (signal = a, vars = siginfo.split(','), e = 0; e < vars.length; e++)
            {
                v = vars[e];
                window[(!isNaN(v[0]) ? "_" : "" ) + v] = a[v];
            }

            is_umts = (network_type == "HSPA" || network_type == "HSDPA" || network_type == "HSUPA" || network_type == "HSPA+" || network_type == "DC-HSPA+" || network_type == "WCDMA" || network_type == "DC" || network_type == "DC-HSPA" || network_type == "CDMA2000" || 
                       network_type == "UMTS" || network_type == "CDMA" || network_type == "CDMA_EVDO" || network_type == "EVDO_EHRPD" || network_type == "TDSCDMA");

            // MC801 = EN-DC, MC801A = ENDC
            is_lte = (network_type == "LTE" || network_type == "ENDC" || network_type == "EN-DC" || network_type == "LTE-NSA" || network_type == "LTE_NSA");
            is_lte_plus = (wan_lte_ca && (wan_lte_ca == "ca_activated" || wan_lte_ca == "ca_deactivated"));

            is_5g_sa = (network_type == "SA");
            is_5g_nsa = (network_type == "ENDC" || network_type == "EN-DC" || network_type == "LTE-NSA" || network_type == "LTE_NSA");
            is_5g_nsa_active = is_5g_nsa && network_type != "LTE-NSA" && network_type != "LTE_NSA";
            is_5g = is_5g_sa || is_5g_nsa;

            if (is_umts) $("#umts_signal_container").show();
            else $("#umts_signal_container").hide();

            if (is_lte_plus) $("#lte_ca_active_tr").show();
            else $("#lte_ca_active_tr").hide();

            if (network_provider_fullname != "") $("#provider").show();
            else $("#provider").hide();

            if (cell_id) {
                const decimalValue = parseInt(cell_id, 16);
                // Only show if conversion is valid
                if (!isNaN(decimalValue)) {
                    $("#cell").show();
                    $("#cell_id").text(decimalValue);
                    // Also update the global variable to be in decimal
                    window.cell_id = decimalValue.toString();
                } else {
                    console.error("Invalid hex value:", cell_id);
                    $("#cell").hide();
                }
            } else {
                $("#cell").hide();
            }


            if (is_5g && nr5g_cell_id && !isNaN(parseInt(nr5g_cell_id, 16))) nr5g_cell_id = parseInt(nr5g_cell_id, 16).toString();
            if (is_5g && nr5g_cell_id) $("#5g_cell").show();
            else $("#5g_cell").hide();

            if (tx_power != "" && is_lte && !is_5g_nsa /* Prevent showing an outdated value from an LTE session */)
            {
                tx_power += " dBm (" + Math.pow(10, tx_power/10.0).toFixed(3) + " mW)";
                $("#txp").show();
            }
            else $("#txp").hide();
            
            $("#ca_active").html(wan_lte_ca && wan_lte_ca == "ca_activated" ? "&#10003;" : "&#10005;");

            /*
             * LTE Cell Info
             */

            var lte_cells = parse_lte_cell_info();

            var2html("__lte_signal", lte_cells);

            for (var i = 0; i < 6; i++)
            {
                var cell_num = i + 1;
                if (is_lte && lte_cells.length > i)
                {
                    var lte_cell = lte_cells[i];
                    if (lte_cell.rsrp1 != "")
                    {
                        $("#lte_" + cell_num + "_rsrp").show();
                        $("#lte_" + cell_num + "_sinr").show();
                        $("#lte_" + cell_num + "_rsrq").show();
                    }
                    else
                    {
                        $("#lte_" + cell_num + "_rsrp").hide();
                        $("#lte_" + cell_num + "_sinr").hide();
                        $("#lte_" + cell_num + "_rsrq").hide();
                    }
                    $("#lte_" + cell_num).show();
                }
                else $("#lte_" + cell_num).hide();
            }

            var lte_bands = get_band_info(lte_cells);

            /*
             * LTE Cell Info End
             */

            /* 
             * NR Cell Info
             */

            var nr_cells = parse_nr_cell_info();

            var2html("__nr_signal", nr_cells);
        
            for (var i = 1; i <= 3; i++)
            {
                if (is_5g && nr_cells.length >= i) $("#5g_" + i).show();
                else $("#5g_" + i).hide();
            }

            if (nr_cells.length > 0)
            {
                if (nr_cells[0].rsrp2 != "") $("#5g_1_rsrp2").show();
                else $("#5g_1_rsrp2").hide();

                if (nr_cells[0].rsrp != "") $("#5g_1_rsrp").show();
                else $("#5g_1_rsrp").hide();

                // Not available with NSA
                if (nr_cells[0].bandwidth != "") $("#5g_1_bandwidth").show();
                else $("#5g_1_bandwidth").hide();
            }

            var nr_bands = get_band_info(nr_cells);
            
            /*
             * NR Cell Info End
             */

            /*
             * Band info
             */

            var bandinfo = lte_bands;

            if (nr_bands != "")
            {
                if (bandinfo != "") bandinfo += " + ";
                bandinfo += nr_bands;
            }

            if (bandinfo != "")
            {
                $("#__bandinfo").html(bandinfo);
                $("#bandinfo").show();
            }
            else $("#bandinfo").hide();

            /*
             * Band info end
             */

            if (is_umts && lte_ca_pcell_band)
                $("#umts_signal_table_main_band").html(" (" + lte_ca_pcell_band + ")");

            if (ngbr_cell_info || Object.keys(ngbr_seen).length > 0)
            {
                ngbr_cell_info = render_ngbr_cells(ngbr_cell_info || "");
                $("#ngbr_cells").show();
            }
            else
            {
                $("#ngbr_cells").hide();
            }

            // Timing advance -> distance to serving LTE cell (1 TA = 16 Ts ~ 78 m one-way)
            if (lte_ta !== "" && lte_ta !== undefined && is_lte && !isNaN(lte_ta))
            {
                lte_ta = lte_ta + " (~" + Math.round(parseInt(lte_ta) * 78.12) + " m)";
                $("#ta_row").show();
            }
            else $("#ta_row").hide();

            if (system_uptime)
            {
                var up = parseInt(system_uptime);
                system_uptime = Math.floor(up / 86400) + "d " + new Date(up * 1000).toISOString().substr(11, 8);
            }

            // signal_quality: firmware's own grade, as shown by ZTE's installer app (0..4)
            var SQ = ["Poor", "Medium", "Good", "Excellent", "Excellent+"];
            if (signal_quality !== "" && SQ[signal_quality]) signal_quality = signal_quality + "/4 (" + SQ[signal_quality] + ")";

            total_rx_bytes = (parseInt(total_rx_bytes || 0) / 1e9).toFixed(1);
            total_tx_bytes = (parseInt(total_tx_bytes || 0) / 1e9).toFixed(1);
            if (total_time) total_time = Math.round(parseInt(total_time) / 3600) + " h";

            realtime_rx_thrpt = (parseInt(realtime_rx_thrpt || 0) * 8 / 1e6).toFixed(2);
            realtime_tx_thrpt = (parseInt(realtime_tx_thrpt || 0) * 8 / 1e6).toFixed(2);

            tools_feed(a);
            $("#lock_status").html(lock_status_html());

            if (wan_ipaddr) $("#wanipinfo").show();
            else $("#wanipinfo").hide();
            if (dns_mode === "manual") $("#manual-dns-info").show();
            else $("#manual-dns-info").hide();

            if (pm_sensor_ambient || pm_sensor_mdm || pm_sensor_5g || pm_sensor_pa1 || wifi_chip_temp || pm_modem_5g)
            {
                var temp = "";
                if (pm_sensor_ambient && pm_sensor_ambient > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "A:&nbsp;" + pm_sensor_ambient + "°c";
                if (pm_sensor_mdm && pm_sensor_mdm > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "M:&nbsp;" + pm_sensor_mdm + "°c";
                if (pm_sensor_5g && pm_sensor_5g > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "5G:&nbsp;" + pm_sensor_5g + "°c";
                if (pm_modem_5g && pm_modem_5g > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "M5G:&nbsp;" + pm_modem_5g + "°c";
                if (pm_sensor_pa1 && pm_sensor_pa1 > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "P:&nbsp;" + pm_sensor_pa1 + "°c";
                if (wifi_chip_temp && wifi_chip_temp > -40) temp += (temp ? "&nbsp;&nbsp;" : "") + "W:&nbsp;" + wifi_chip_temp + "°c";
                $("#temps").html(temp);
                $("#temperature").show();
            } 
            else $("#temperature").hide();

            for (e = 0; e < vars.length; e++)
            {
                v = vars[e];
                v = (!isNaN(v[0]) ? "_" : "" ) + v;
                $("#" + v).html(window[v]);
            }
        }
    })
}

function err(a, e, n)
{
    alert("Communication Error"), console.log(a), console.log(e), console.log(n)
}

function set_net_mode(mode = null)
{
    var modes = [
        "Only_GSM",
        "Only_WCDMA",
        "Only_LTE",
        "WCDMA_AND_GSM",
        "WCDMA_preferred",
        "WCDMA_AND_LTE",
        "GSM_AND_LTE",
        "CDMA_EVDO_LTE",
        "Only_TDSCDMA",
        "TDSCDMA_AND_WCDMA",
        "TDSCDMA_AND_LTE",
        "TDSCDMA_WCDMA_HDR_CDMA_GSM_LTE",
        "TDSCDMA_WCDMA_GSM_LTE",
        "GSM_WCDMA_LTE",
        "Only_5G",
        "LTE_AND_5G",
        "GWL_5G",
        "TCHGWL_5G",
        "WL_AND_5G",
        "TGWL_AND_5G",
        "4G_AND_5G"
    ];

    mode = mode || prompt("Enter one of\n" + modes.join(", "), "WL_AND_5G");
    if (!mode) return;

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD);
            $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data:
                {
                    isTest: "false",
                    goformId: "SET_BEARER_PREFERENCE",
                    BearerPreference: mode,
                    AD: ad
                },
                success: function(a)
                {
                    console.log(a);
                    j = JSON.parse(a);
                    if ("success" != j.result)
                        alert("Setting mode to '" + mode + "' failed");
                },
                error: err
            })
        }
    })

}

function lte_cell_lock(reset = false, preset = null) {
    var lockParameters;

    if (reset) {
        lockParameters = ["0", "0"];
    } else {
        var defaultPciEarfcn = parseInt(lte_pci, 16) + "," + wan_active_channel;
        var cellLockDetails = preset
            ? (confirm("Lock LTE to PCI " + preset.split(",")[0] + " on EARFCN " + preset.split(",")[1] + "?") ? preset : null)
            : prompt("Please input PCI,EARFCN, separated by ',' char (example 116,3350). "+ 
                     "Leave default for lock on current main band.", defaultPciEarfcn);

        if (cellLockDetails === null || cellLockDetails.trim() === "") {
            return;
        }

        var inputValues = cellLockDetails.split(",").map(v => v.trim());
        var pciIsValid = /^\d+$/.test(inputValues[0]) && parseInt(inputValues[0]) <= 503;
        var earfcnIsValid = /^\d+$/.test(inputValues[1]) && parseInt(inputValues[1]) <= 68935;

        if (!pciIsValid || !earfcnIsValid) {
            alert("Invalid input. Please ensure all values are correctly formatted.");
            return;
        }

        lockParameters = inputValues;
    }

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data: {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a) {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD);
            $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data: {
                    isTest: "false",
                    goformId: "LTE_LOCK_CELL_SET",
                    lte_pci_lock: lockParameters[0],
                    lte_earfcn_lock: lockParameters[1],
                    AD: ad
                },
                success: function(a) {
                    var response = JSON.parse(a);
                    if (response.result === "success") {
                        if (!reset) exp_arm(false, lockParameters[1], lockParameters[0], "LTE lock PCI " + lockParameters[0] + " @ " + lockParameters[1]);

                        var rebootMessage = 
                            "You have to reboot your Router in order " + 
                            (reset ? "to remove the cell lock" : "for the cell lock to be active") + ".\n\nReboot now?";

                        if (confirm(rebootMessage)) {
                            reboot(true);
                        }
                    } else {
                        alert("Error.");
                    }
                },
                error: function(err) {
                    console.error(err);
                    alert("An error occurred while attempting to lock the cell.");
                }
            });
        }
    });
}

function nr_cell_lock(reset = false, preset = null, ask = false) {
    var cellLockDetails;

    if (reset) {
        cellLockDetails = "0,0,0,0";
    } else {
        var nrCellInfo = parse_nr_cell_info();
        var defaultCellDetails = "";

        if (nrCellInfo.length > 0) {
            var primaryNrCell = nrCellInfo[0];
            defaultCellDetails = primaryNrCell.pci + ',' + primaryNrCell.arfcn + ',' + primaryNrCell.band.replace('n', '') + ',' + "30";
        }

        if (preset && !ask) {
            var v = preset.split(",");
            cellLockDetails = confirm("Lock 5G to PCI " + v[0] + ", ARFCN " + v[1] + ", band n" + v[2] + ", SCS " + v[3] + " kHz?") ? preset : null;
        } else {
            cellLockDetails = prompt("Please input PCI,ARFCN,BAND,SCS separated by ',' char (example 202,639936,78,30). " + 
                                     "Leave default for locking the current NR primary band. You may need to adjust the SCS.", preset || defaultCellDetails);
        }

        if (cellLockDetails === null || cellLockDetails.trim() === "") {
            return;
        } else {
            var inputValues = cellLockDetails.split(",").map(v => v.trim().replace(/^n/i, ""));

            var pciIsValid = /^\d+$/.test(inputValues[0]) && parseInt(inputValues[0]) <= 1007;
            var arfcnIsValid = /^\d+$/.test(inputValues[1]);
            var bandIsValid = /^\d+$/.test(inputValues[2]);
            var scsIsValid = ["15", "30", "60", "120", "240"].includes(inputValues[3]);

            if (!pciIsValid || !arfcnIsValid || !bandIsValid || !scsIsValid) {
                alert("Invalid input. Please ensure all values are correctly formatted.");
                return;
            }
            cellLockDetails = inputValues.slice(0, 4).join(",");
        }
    }

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data: {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a) {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD);
            $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data: {
                    isTest: "false",
                    goformId: "NR5G_LOCK_CELL_SET",
                    nr5g_cell_lock: cellLockDetails,
                    AD: ad
                },
                success: function(a) {
                    var response = JSON.parse(a);
                    if (response.result === "success") {
                        if (!reset) { var lv = cellLockDetails.split(","); exp_arm(true, lv[1], lv[0], "5G lock PCI " + lv[0] + " @ " + lv[1]); }

                        var rebootMessage = 
                            "You have to reboot your Router in order " + 
                            (reset ? "to remove the cell lock" : "for the cell lock to be active")+ ".\n\nReboot now?";

                        if (confirm(rebootMessage)) {
                            reboot(true);
                        }
                    } else {
                        alert("Error.");
                    }
                },
                error: function(err) {
                    console.error(err);
                    alert("An error occurred while attempting to lock the cell.");
                }
            });
        }
    });
}

function lte_band_selection(a = null, nested_attempt_with_dev_login = false)
{
    a = a || prompt("Please input LTE bands number, separated by + char (example 1+3+20). If you want to use every supported band, write 'AUTO'.", "AUTO");
    var bands_input = a;

    var had_admin_password_hash = have_admin_password_hash();

    if (null != (a = a && a.toLowerCase()) && "" !== a)
    {
        var e = a.split("+");
        var n = 0;
        var all_bands = "0xA3E2AB0908DF";

        if ("AUTO" === a.toUpperCase())
        {
            n = all_bands;
        }
        else
        {
            var bands = Array.from(new Set(e.map(b => parseInt(b.trim().replace(/^b/, "")))));
            if (bands.some(b => isNaN(b) || b < 1 || b > 52))
            {
                alert("Invalid LTE band list: " + a);
                return;
            }
            bands.forEach(b => n += Math.pow(2, b - 1));
            n = "0x" + n.toString(16).padStart(11, "0");
        }

        $.ajax({
            type: "GET",
            url: "/goform/goform_get_cmd_process",
            data:
            {
                cmd: "wa_inner_version,cr_version,RD",
                multi_data: "1"
            },
            dataType: "json",
            success: function(a)
            {
                ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD), $.ajax({
                    type: "POST",
                    url: "/goform/goform_set_cmd_process",
                    data:
                    {
                        isTest: "false",
                        goformId: "BAND_SELECT",
                        is_gw_band: 0,
                        gw_band_mask: 0,
                        is_lte_band: 1,
                        lte_band_mask: n,
                        AD: ad
                    },
                    success: function(a)
                    {
                        console.log(a);

                        var j = JSON.parse(a);
   
                        if ("success" == j.result)
                        {
                            if (nested_attempt_with_dev_login)
                            {
                                if (!had_admin_password_hash)
                                    alert("Successfully performed LTE band lock with developer login ...");
                            }
                        }
                        else
                        {
                            if (!nested_attempt_with_dev_login && !logged_in_as_developer)
                            {
                                if (!had_admin_password_hash)
                                {
                                    alert("LTE band locking failed.\n\n" +
                                          "Your device model may require to log in as developer\n" + 
                                          "in order to be able to lock LTE bands.");
                                }

                                perform_login(
                                    function() {
                                        logged_in_as_developer = true;
                                        lte_band_selection(bands_input, true);
                                    }, true);
                            }
                            else
                            {
                                alert("LTE band locking with developer login still failed.\nThere might be something else wrong.");
                            }
                        }
                    },
                    error: err
                })
            }
        })
    }
}

function nr_band_selection(a)
{
    var e;
    var a = a || prompt("Please input 5G bands number, separated by + char (example 3+78). If you want to use every supported band, write 'AUTO'.", "AUTO");

    if (a == null || a.trim() === "") return;
    e = a.split("+").map(b => b.trim().replace(/^n/i, "")).join(",");
    "AUTO" === a.trim().toUpperCase() && (e = "1,2,3,5,7,8,20,28,38,41,50,51,66,70,71,74,75,76,77,78,79,80,81,82,83,84");
    if (!/^\d+(,\d+)*$/.test(e))
    {
        alert("Invalid 5G band list: " + a);
        return;
    }

    $.ajax({
            type: "GET",
            url: "/goform/goform_get_cmd_process",
            data:
            {
                cmd: "wa_inner_version,cr_version,RD",
                multi_data: "1"
            },
            dataType: "json",
            success: function(a)
            {
                ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD), $.ajax({
                    type: "POST",
                    url: "/goform/goform_set_cmd_process",
                    data:
                    {
                        isTest: "false",
                        goformId: "WAN_PERFORM_NR5G_BAND_LOCK",
                        nr5g_band_mask: e,
                        AD: ad
                    },
                    success: function(a)
                    {
                        console.log(a);
                        if (JSON.parse(a).result != "success") alert("5G band locking failed.");
                    },
                    error: err
                })
            }
    });
}

function bridge_mode(enable)
{
    if (!confirm((enable ? "Enable" : "Disable") + " bridge mode and reboot router?"))
        return;

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD), $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data:
                {
                    isTest: "false",
                    goformId: "OPERATION_MODE",
                    opMode:	(enable ? "LTE_BRIDGE" : "PPP"),
                    ethernet_port_specified: "1",
                    AD: ad
                },
                success: function(a)
                {
                    console.log(a);
                    if (JSON.parse(a).result != "success") { alert("Bridge mode change failed."); return; }
                    alert("Successfully " + (enable ? "enabled" : "disabled") + " bridge mode! Rebooting ..." +
                          (enable ? "\n\nIf your device has multiple LAN port then the lower one\nis the WAN/bridge port!" : ""));
                    reboot(true);
                },
                error: err
            })
        }
    })
}

function arp_proxy(enable)
{
    if (!confirm((enable ? "Enable" : "Disable") + " ARP proxy and reboot router?"))
        return;

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD), $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data:
                {
                    isTest: "false",
                    goformId: "ARP_PROXY_SWITCH",
                    arp_proxy_switch: enable ? 1 : 0,
                    AD: ad
                },
                success: function(a)
                {
                    console.log(a);
                    if (JSON.parse(a).result != "success") { alert("ARP proxy change failed (not supported by this firmware?)."); return; }
                    alert((enable ? "Enabled" : "Disabled") + " ARP proxy!");
                    reboot(true);
                },
                error: err
            })
        }
    })
}

function reboot(force = false)
{
    if (!force && !confirm("Reboot Router?"))
        return

    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            ad = hash(hash(a.wa_inner_version + a.cr_version) + a.RD), $.ajax({
                type: "POST",
                url: "/goform/goform_set_cmd_process",
                data:
                {
                    isTest: "false",
                    goformId: "REBOOT_DEVICE",
                    AD: ad
                },
                success: function(a)
                {
                    console.log(a);
                    if (!force) alert("Rebooting ...");
                },
                error: err
            })
        }
    })
}

function version_info()
{
    $.ajax({
        type: "GET",
        url: "/goform/goform_get_cmd_process",
        data:
        {
            cmd: "hardware_version,web_version,wa_inner_version,cr_version,RD",
            multi_data: "1"
        },
        dataType: "json",
        success: function(a)
        {
            v = "HW version: " + a.hardware_version + "\nWEB version: " + a.web_version + "\nWA INNER version: " + a.wa_inner_version;
            alert(v);
        }
    })
}

function inject_main_container_if_missing() {
    // Newer models like the MC888 Ultra don't have a main container anymore.
    // Inject a fake one to get the script working.

    if (!$("#mainContainer").length) {
        $("body").prepend(`
            <div id="mainContainer" align="center">
                <style>
                    #mainContainer table {
                        margin: 0 auto;
                        text-align: left;
                    }
                    #mainContainer a {
                        color: #007bff;
                        text-decoration: none;
                        cursor: pointer;
                    }
                    #mainContainer a:hover {
                        text-decoration: underline;
                    }
                </style>
            </div>
        `);
    }
}

function inject_html()
{
    inject_main_container_if_missing();

    $(".color_background_blue").css("background-color", "#456");
    $(".headcontainer").hide();

    $("#mainContainer").prepend(`
    <style>
        
    .clear {
        clear: both;
    }
    
    li span {
        margin-left: 5px;
    }

    .f {
        /*float: left;*/
        border: 1px solid #bbb;
        border-radius: 5px;
        padding: 10px;
        line-height: 2em;
        margin: 5px;
    }
    
    .f ul {
        margin: 0;
        padding: 0;
    }
    
    .f ul li {
        display: inline;
        margin-right: 5px;
        margin-left: 5px;
    }
    
    .p {
        border-bottom: 1px solid #ccc;
        width: auto;
        height: 20px;
    }
    
    .v {
        height: 100%25;
        border-right: 1px solid #ccc;
    }
    
    .sb {
        padding: 10px;
        border-radius: 10px;
        display: inline-block;
        margin: 10px 0 10px 10px;
    }
    
    .v {
        padding-left: 20px;
    }

    .mod_border {
        border-radius: 5px;
        border-style: hidden;
        box-shadow: 0 0 0 3px #999;
    }

    .mod_container {
        width: 940px;
        border: 4px solid #40adf5;
        border-radius: 10px;
        padding: 5px;
        font-family: Verdana;
        font-size: 13px;
    }

    .inner_mod_container {
        width: 900px;  /* was 600px; NGBR table has 11 columns */
        margin: 0 auto;
    }

    .mod_table {
        all: revert;
        border-collapse: collapse;

        border-radius: 5px;
        border-style: hidden;
        box-shadow: 0 0 0 3px #999;
    }

    .mod_table td {
        border: 3px solid #999;
        padding: 5px;
        border-radius: 20px;
    }

    .ngbr_wrap {
        overflow-x: auto;
        margin-top: 5px;
    }

    .ngbr_cell_table {
        all: revert;
        border: none;
        width: 100%;
        border-collapse: collapse;
    }

    .tools_table {
        all: revert;
        border-collapse: collapse;
        margin-top: 5px;
    }

    .tools_table td, .tools_table th {
        all: revert;
        padding: 1px 10px 1px 0;
        white-space: nowrap;
        text-align: left;
    }

    .tools_container summary {
        cursor: pointer;
        margin: 4px 0;
    }

    .ngbr_cell_table td, .ngbr_cell_table th {
        all: revert;
        border: none;
        padding: 2px 8px 2px 0;
        white-space: nowrap;
        text-align: left;
    }

    .signal_table {
        width: 100%;
    }

    .signal_table td {
        width: 75px;
    }

    .cellinfo_table {
        width: 100%;
        table-layout: fixed;
    }


    .spacing {
        padding: 10px;
    }

    .spacing_small {
        padding: 5px;
    }

    .spacing_links {
        padding: 1px;
    }

    .links_container {
        font-size: 14px;
        text-align: left;
    }

    </style>

    <div class="mod_container">
        <div class="spacing_small"></div>

        <div class="inner_mod_container">
            <!-- LTE Primary -->
            <div id="lte_1">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='4' style='text-align:center'>LTE (<span id="__lte_signal_0_band"></span>)</td>
                    </tr>
                    <tr>
                        <td>RSRP1:</td>
                        <td><span id="__lte_signal_0_rsrp1"></span> dBm</td>
                        <td>SINR1:</td>
                        <td><span id="__lte_signal_0_sinr1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRP2:</td>
                        <td><span id="__lte_signal_0_rsrp2"></span> dBm</td>
                        <td>SINR2:</td>
                        <td><span id="__lte_signal_0_sinr2"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRP3:</td>
                        <td><span id="__lte_signal_0_rsrp3"></span> dBm</td>
                        <td>SINR3:</td>
                        <td><span id="__lte_signal_0_sinr3"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRP4:</td>
                        <td><span id="__lte_signal_0_rsrp4"></span> dBm</td>
                        <td>SINR4:</td>
                        <td><span id="__lte_signal_0_sinr4"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_0_rsrq"></span> dB</td>
                        <td>RSSI:</td>
                        <td><span id="__lte_signal_0_rssi"></span> dBm</td>
                    </tr>
                    <tr id="lte_1_earfcn">
                        <td colspan='2'>EARFCN:</td>
                        <td colspan='2'><span id="__lte_signal_0_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td colspan='2'>PCI:</td>
                        <td colspan='2'><span id="__lte_signal_0_pci"></span></td>
                    </tr>
                    <tr>
                        <td colspan='2'>4G RSRP:</td>
                        <td colspan='2'><span id="lte_rsrp"></span> dB</td>
                    </tr>
                    <tr>
                        <td colspan='2'>4G RSSI:</td>
                        <td colspan='2'><span id="lte_rssi"></span> dB</td>
                    </tr>
                    <tr>
                        <td colspan='2'>4G RSRQ:</td>
                        <td colspan='2'><span id="lte_rsrq"></span> dB</td>
                    </tr>
                    <tr>
                        <td colspan='2'>4G SINR:</td>
                        <td colspan='2'><span id="lte_snr"></span> dB</td>
                    </tr>
                    <tr>
                        <td colspan='2'>BW:</td>
                        <td colspan='2'><span id="bandwidth"></span></td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>

            <div id="lte_2">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>LTE (<span id="__lte_signal_1_band"></span>)</td>
                    </tr>
                    <tr id="lte_2_rsrp">
                        <td>RSRP:</td>
                        <td><span id="__lte_signal_1_rsrp1"></span> dBm</td>
                    </tr>
                    <tr id="lte_2_sinr">
                        <td>SINR:</td>
                        <td><span id="__lte_signal_1_sinr1"></span> dB</td>
                    </tr>
                    <tr id="lte_2_rsrq">
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_1_rsrq"></span> dB</td>
                    </tr>
                    <tr id="lte_2_earfcn">
                        <td>EARFCN:</td>
                        <td><span id="__lte_signal_1_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__lte_signal_1_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__lte_signal_1_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>
            <div id="lte_3">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>LTE (<span id="__lte_signal_2_band"></span>)</td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__lte_signal_2_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__lte_signal_2_sinr1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_2_rsrq"></span> dB</td>
                    </tr>
                    <tr id="lte_3_earfcn">
                        <td>EARFCN:</td>
                        <td><span id="__lte_signal_2_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__lte_signal_2_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__lte_signal_2_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>
            <div id="lte_4">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>LTE (<span id="__lte_signal_3_band"></span>)</td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__lte_signal_3_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__lte_signal_3_sinr1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_3_rsrq"></span> dB</td>
                    </tr>
                    <tr id="lte_4_earfcn">
                        <td>EARFCN:</td>
                        <td><span id="__lte_signal_3_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__lte_signal_3_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__lte_signal_3_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>
            <div id="lte_5">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>LTE (<span id="__lte_signal_4_band"></span>)</td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__lte_signal_4_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__lte_signal_4_sinr1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_4_rsrq"></span> dB</td>
                    </tr>
                    <tr id="lte_5_earfcn">
                        <td>EARFCN:</td>
                        <td><span id="__lte_signal_4_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__lte_signal_4_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__lte_signal_4_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>
            <div id="lte_6">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>LTE (<span id="__lte_signal_5_band"></span>)</td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__lte_signal_5_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__lte_signal_5_sinr1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__lte_signal_5_rsrq"></span> dB</td>
                    </tr>
                    <tr id="lte_6_earfcn">
                        <td>EARFCN:</td>
                        <td><span id="__lte_signal_5_earfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__lte_signal_5_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__lte_signal_5_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>


            <div id="umts_signal_container">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='4' style='text-align:center'>UMTS<span id="umts_signal_table_main_band"></span></td>
                    </tr>
                    <tr>
                        <td>RSCP1:</td>
                        <td><span id="rscp_1"></span> dBm</td>
                        <td>ECIO1:</td>
                        <td>-<span id="ecio_1"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSCP2:</td>
                        <td><span id="rscp_2"></span> dBm</td>
                        <td>ECIO2:</td>
                        <td>-<span id="ecio_2"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSCP3:</td>
                        <td><span id="rscp_3"></span> dBm</td>
                        <td>ECIO3:</td>
                        <td>-<span id="ecio_3"></span> dB</td>
                    </tr>
                    <tr>
                        <td>RSCP4:</td>
                        <td><span id="rscp_4"></span> dBm</td>
                        <td>ECIO4:</td>
                        <td>-<span id="ecio_4"></span> dB</td>
                    </tr>
                    <tr>
                        <td colspan='2'>3G ECIO</td>
                        <td colspan='2'><span id="ecio"></span> dB</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>          
    
            <!-- NR Primary -->
            <div id="5g_1">
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>
                            5G (<span id="__nr_signal_0_band"></span>)
                            <span id="__nr_signal_0_info_text"></span>
                        </td>
                    </tr>
                    <tr>
                        <td>RSRP1:</td>
                        <td><span id="__nr_signal_0_rsrp1"></span> dBm</td>
                    </tr>
                    <tr id="5g_1_rsrp2">
                        <td>RSRP2:</td>
                        <td><span id="__nr_signal_0_rsrp2"></span> dBm</td>
                    </tr>
                    <tr id="5g_1_rsrp">
                        <td>GLOBAL RSRP:</td>
                        <td><span id="__nr_signal_0_rsrp"></span> dBm (> -81 Excellent)</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__nr_signal_0_rsrq"></span> dB</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__nr_signal_0_sinr"></span> dB</td>
                    </tr>
                    <tr id="5g_1_arfcn">
                        <td>ARFCN:</td>
                        <td><span id="__nr_signal_0_arfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__nr_signal_0_pci"></span></td>
                    </tr>
                    <tr id="5g_1_bandwidth">
                        <td>BW:</td>
                        <td><span id="__nr_signal_0_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>

            <div id="5g_2">
                <!-- NR Scell1 -->
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>
                            5G (<span id="__nr_signal_1_band"></span>)
                            <span id="__nr_signal_1_info_text"></span>
                        </td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__nr_signal_1_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__nr_signal_1_rsrq"></span> dB</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__nr_signal_1_sinr"></span> dB</td>
                    </tr>
                    <tr id="5g_2_arfcn">
                        <td>ARFCN:</td>
                        <td><span id="__nr_signal_1_arfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__nr_signal_1_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__nr_signal_1_bandwidth"></span> MHz</td>
                    </tr>
                </table>
                <div class="spacing"></div>
            </div>
            <div id="5g_3">
                <!-- NR Scell2 -->
                <table class="mod_table signal_table">
                    <tr>
                        <td colspan='2' style='text-align:center'>
                            5G (<span id="__nr_signal_2_band"></span>)
                            <span id="__nr_signal_2_info_text"></span>
                        </td>
                    </tr>
                    <tr>
                        <td>RSRP:</td>
                        <td><span id="__nr_signal_2_rsrp1"></span> dBm</td>
                    </tr>
                    <tr>
                        <td>RSRQ:</td>
                        <td><span id="__nr_signal_2_rsrq"></span> dB</td>
                    </tr>
                    <tr>
                        <td>SINR:</td>
                        <td><span id="__nr_signal_2_sinr"></span> dB</td>
                    </tr>
                    <tr id="5g_3_arfcn">
                        <td>ARFCN:</td>
                        <td><span id="__nr_signal_2_arfcn"></span></td>
                    </tr>
                    <tr>
                        <td>PCI:</td>
                        <td><span id="__nr_signal_2_pci"></span></td>
                    </tr>
                    <tr>
                        <td>BW:</td>
                        <td><span id="__nr_signal_2_bandwidth"></span> MHz</td>
                    </tr>
                </table>

                <div class="spacing"></div>
            </div>

            <div>    
                <table class="mod_table cellinfo_table">
                    <tr id="provider">
                        <td>PROVIDER:</td>
                        <td><span id="network_provider_fullname"></span></td>
                    </tr>
                    <tr id="cell">
                        <td>CELL:</td>
                        <td><span id="cell_id"></span></td>
                    </tr>
                    <tr id="5g_cell">
                        <td>5G CELL:</td>
                        <td><span id="nr5g_cell_id"></span></td>
                    </tr>
                    <tr id="ngbr_cells">
                        <td colspan="2">NGBR:<div class="ngbr_wrap"><span id="ngbr_cell_info"></span></div></td>
                    </tr>
                    <tr>
                        <td>LOCKS:</td>
                        <td id="lock_status"></td>
                    </tr>
                    <tr>
                        <td>ANT. BRANCHES:</td>
                        <td class="branch_warn"></td>
                    </tr>
                    <tr id="ta_row">
                        <td>LTE TA:</td>
                        <td><span id="lte_ta"></span></td>
                    </tr>
                    <tr id="txp">
                        <td>TX POWER:</td>
                        <td><span id="tx_power"></span></td>
                    </tr>
                    <tr>
                        <td>CONNECTION:</td>
                        <td><span id="network_type"></span></td>
                    </tr>
                    <tr id="bandinfo">
                        <td>BANDS:</td>
                        <td>
                            <span id="__bandinfo">
                        </td>
                    </tr>
                    <tr id="lte_ca_active_tr">
                        <td>LTE CA ACTIVE:</td>
                        <td><span id="ca_active"></span></td>
                    </tr>
                    <tr id="wanipinfo">
                        <td>WAN IP:</td>
                        <td><span id="wan_ipaddr"></span></td>
                    </tr>
                    <tr id="manual-dns-info">
                        <td>WAN MANUAL DNS:</td>
                        <td><span id="prefer_dns_manual"></span></td>
                    </tr>
                    <tr id="temperature">
                        <td>TEMP:</td>
                        <td id="temps"></td>
                    </tr>
                    <tr>
                        <td>TRAFFIC:</td>
                        <td>&darr;&nbsp;<span id="realtime_rx_thrpt"></span>&nbsp;Mbit/s&nbsp;&nbsp;&uarr;&nbsp;<span id="realtime_tx_thrpt"></span>&nbsp;Mbit/s</td>
                    </tr>
                    <tr>
                        <td>QUALITY:</td>
                        <td><span id="signal_quality"></span></td>
                    </tr>
                    <tr>
                        <td>TOTAL TRAFFIC:</td>
                        <td>&darr;&nbsp;<span id="total_rx_bytes"></span>&nbsp;GB&nbsp;&nbsp;&uarr;&nbsp;<span id="total_tx_bytes"></span>&nbsp;GB&nbsp;&nbsp;(<span id="total_time"></span>)</td>
                    </tr>
                    <tr>
                        <td>UPTIME:</td>
                        <td><span id="system_uptime"></span></td>
                    </tr>
                    <tr id="device_extras_row" style="display:none">
                        <td>DEVICE:</td>
                        <td id="device_extras"></td>
                    </tr>
                </table>
            </div>

        </div>

        <div class="spacing"></div>

        <div class="inner_mod_container mod_border tools_container">
            <details open>
                <summary><b>Antenna alignment</b></summary>
                <label><input type="checkbox" onchange="align_fast(this.checked)"> Fast update (0.5 s)</label>
                &nbsp;&nbsp;
                <label><input type="checkbox" onchange="tone_toggle(this.checked)"> Tone</label>
                <select onchange="tone_set_mode(this.value)">
                    <option value="dual" selected>5G + LTE SINR</option>
                    <option value="balance">5G rx0/rx1 balance</option>
                    ${ALIGN_METRICS.slice(0, 8).map(m => "<option value='m:" + m[0] + "'>" + m[2] + "</option>").join("")}
                </select>
                <select onchange="tone_set_res(this.value)" title="High / very high: pitch follows the change from the reference (Reset start/peak) - 2 or 4 semitones per dB">
                    <option value="abs">Absolute</option>
                    <option value="high" selected>High res (2 st/dB)</option>
                    <option value="vhigh">Very high (4 st/dB)</option>
                </select>
                &nbsp;&nbsp;
                <a onclick="tools_reset()">Reset start/peak</a> | <a onclick="position_save()">Save position</a>
                <div style="font-size:11px">Tone: <span id="tone_legend">${TONE_LEGEND.dual}</span>
                    High / very high resolution: pitch is relative to the reference set by Reset start/peak (1 dB = 2 / 4 semitones).</div>
                <div class="spacing_links"></div>
                Antenna branches: <span class="branch_warn"></span>
                <table class="tools_table" id="align_table"></table>
            </details>
            <details>
                <summary><b>Saved positions</b></summary>
                <a onclick="csv_export('zte_positions', 'zte-positions.csv')">Export CSV</a> |
                <a onclick="records_clear('zte_positions', positions_render)">Clear</a>
                <table class="tools_table" id="positions_table"></table>
            </details>
            <details>
                <summary><b>Cell evaluations / lock experiments</b></summary>
                <a onclick="exp_measure_now()">Measure current cell (60 s)</a> |
                <a onclick="csv_export('zte_exp_results', 'zte-cell-tests.csv')">Export CSV</a> |
                <a onclick="records_clear('zte_exp_results', exp_render)">Clear</a>
                &nbsp; <span id="exp_state"></span>
                <div style="font-size:11px">Cell locks from this page start a 60 s measurement automatically once the router is camped on the locked cell (after reboot). RX/TX is the traffic flowing at the time, not link capacity - run a speed test during the window to compare.</div>
                <table class="tools_table" id="exp_table"></table>
            </details>
        </div>

        <div class="spacing"></div>

        <div class="inner_mod_container mod_border links_container">
            <a onclick="set_net_mode()">Network Mode</a>
            [
                <a onclick="set_net_mode('WL_AND_5G')">Auto</a> |
                <a onclick="set_net_mode('Only_5G')">5G SA</a> |
                <a onclick="set_net_mode('LTE_AND_5G')">5G NSA</a> |
                <a onclick="set_net_mode('4G_AND_5G')">5G SA/NSA/LTE</a> |
                <a onclick="set_net_mode('Only_LTE')">LTE</a> |
                <a onclick="set_net_mode('Only_WCDMA')">3G</a> |
                <a onclick="set_net_mode('Only_GSM')">2G</a>
            ]
            
            <div class="spacing_links"></div>

            <div id="lte_band_selection">
                <a onclick="lte_band_selection()">LTE Bands</a>
                [
                    <a onclick="lte_band_selection('AUTO')">Auto</a> |
                    <a onclick="lte_band_selection('1')">B1</a> |
                    <a onclick="lte_band_selection('3')">B3</a> |
                    <a onclick="lte_band_selection('7')">B7</a> |
                    <a onclick="lte_band_selection('8')">B8</a> |
                    <a onclick="lte_band_selection('20')">B20</a> |
                    <a onclick="lte_band_selection('1+3')">B1+B3</a> |
                    <a onclick="lte_band_selection('1+3+7')">B1+B3+B7</a>
                ]

                <div class="spacing_links"></div>
            </div>

            <a onclick="nr_band_selection()">5G Bands</a>
            [
                <a onclick="nr_band_selection('AUTO')">Auto</a> |
                <a onclick="nr_band_selection('1')">N1</a> |
                <a onclick="nr_band_selection('3')">N3</a> |
                <a onclick="nr_band_selection('7')">N7</a> |
                <a onclick="nr_band_selection('28')">N28</a> |
                <a onclick="nr_band_selection('28,75')">N28+N75</a> |
                <a onclick="nr_band_selection('78')">N78</a> |
                <a onclick="nr_band_selection('78,28,75')">N78+N28+N75</a>
            ]

            <div class="spacing_links"></div>

            <a onclick="bridge_mode(true)">Enable bridge mode</a> | <a onclick="bridge_mode(false)">Disable bridge mode</a>

            <div class="spacing_links"></div>

            <a onclick="arp_proxy(true)">Enable ARP proxy</a> | <a onclick="arp_proxy(false)">Disable ARP proxy</a>

            <div class="spacing_links"></div>

            <a onclick="make_hidden_settings_visible()">Show hidden device settings</a>
            <div class="spacing_links"></div>

            Hidden pages:
            [
                <a href="#debug_page">Debug</a> |
                <a href="#network_info">Network Info</a> |
                <a href="#ant_settings">Antenna</a> |
                <a href="#temp_status">Temperatures</a> |
                <a href="#bsp_tc_settings">Thermal Thresholds</a> |
                <a href="#rf_mmw">mmWave Modules</a> |
                <a href="#developer_login">Developer</a>
            ]
            <div class="spacing_links"></div>

            <a onclick="enable_automatic_login()">Enable Automatic Login</a> | <a onclick="version_info()">Version Info</a>
            <div class="spacing_links"></div>

            <a onclick="lte_cell_lock()">LTE Cell Lock</a> <span id="lte_cell_lock"></span> |
            <a onclick="lte_cell_lock(true)">Remove LTE Cell Lock</a> <span id="undo_lte_cell_lock"></span> ||
            <a onclick="nr_cell_lock()">5G Cell Lock</a> <span id="nr_cell_lock"></span> |
            <a onclick="nr_cell_lock(true)">Remove 5G Cell Lock</a> <span id="undo_nr_cell_lock"></span>

            <div class="spacing_links"></div>

            Config: <a onclick="config_SHOW_APN_DNS()">APN DNS</a>
            
            <div class="spacing_links"></div>

            <a onclick="reboot()">Reboot Router</a>
            <br>
            
        </div>

        <div class="spacing_small"></div>
    </div>

    <div class="spacing"></div>
    `)
}

function set_config(key, value){
    if (typeof require.s.contexts._.defined["config/config"][key] === "undefined") {
        console.log("Config key not found, setting it anyway: " + key);
    } else {
        console.log("Previous config value: " + require.s.contexts._.defined["config/config"][key]);
    }
    console.log("Setting config: " + key + " to " + value);
    require.s.contexts._.defined["config/config"][key] = value;
}

function config_SHOW_APN_DNS(){
    const value = prompt("Show APN DNS settings? (true/false)", "true");
    set_config("SHOW_APN_DNS", value === "true");
}

prepare_1_timer_id = window.setInterval(prepare_1, 250);
prepare_1();

$("#change").prop("disabled", !1);

$("#umts_signal_container").hide();
for (var i = 1; i <= 3; i++) $("#5g_" + i).hide();
for (var i = 1; i <= 6; i++) $("#lte_" + i).hide();
$("#lte_ca_active_tr").hide();
$("#provider").hide();
$("#cell").hide();
$("#5g_cell").hide();
$("#ngbr_cells").hide();
$("#txp").hide();
$("#temperature").hide();
$("#wanipinfo").hide();
