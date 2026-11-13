const root = document.getElementById("app");
let msg = null;

function el(tag, props, ...kids) {
    const node = Object.assign(document.createElement(tag), props || {});
    node.append(...kids);
    return node;
}

function say(text, isError) {
    msg.textContent = text;
    msg.className = isError ? "error" : "ok";
}

async function api(method, path, body) {
    const res = await fetch(path, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
        throw new Error((data && data.error) || "Request failed (" + res.status + ")");
    }
    return data;
}

// run an action, show its error message instead of crashing
async function attempt(action) {
    try { await action(); } catch (err) { say(err.message, true); }
}

// builds a table from a list
function table(headers, rows) {
    const t = el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))));
    t.append(el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((c) => el("td", {}, c))))));
    return t;
}

// creates welcome page
function shell(user, ...content) {
    msg = el("p", { id: "msg" });
    root.replaceChildren(
        el("header", {},
            el("h1", {}, "Welcome " + user.name + "! ACME University"),
            el("button", { onclick: () => attempt(async () => { await api("POST", "/api/logout"); start(); }) }, "Sign out")),
        msg, ...content);
}

// login page
function showLogin(error) {
    const username = el("input", { placeholder: "Username", autocomplete: "username" });
    const password = el("input", { placeholder: "Password", type: "password", autocomplete: "current-password" });
    const submit = () => attempt(async () => {
        await api("POST", "/api/login", { username: username.value.trim(), password: password.value });
        start();
    });
    password.onkeydown = (e) => { if (e.key === "Enter") submit(); };
    msg = el("p", { id: "msg" });
    root.replaceChildren(el("div", { className: "login" }, el("h1", {}, "ACME University"),
        username, password, el("button", { onclick: submit }, "Sign in"), msg));
    if (error) say(error, true);
}

// student view; your course and add courses
async function showStudent(user, tab) {
    const courses = await api("GET", "/api/courses");
    const count = (c) => c.enrolled + "/" + c.capacity;
    const info = (c) => [c.name, c.teacher, c.time, count(c)];
    const reload = (t) => showStudent(user, t);

    const mine = courses.filter((c) => c.mine).map((c) => info(c).concat(
        el("button", { onclick: () => attempt(async () => {
            await api("DELETE", "/api/enroll/" + c.id); await reload("mine"); say("Dropped " + c.name, false);
        }) }, "Drop")));
    const all = courses.map((c) => info(c).concat(
        c.mine ? "Enrolled" : c.enrolled >= c.capacity ? "Full" :
        el("button", { onclick: () => attempt(async () => {
            await api("POST", "/api/enroll", { course_id: c.id }); await reload("mine"); say("Added " + c.name, false);
        }) }, "Add class")));

    const head = ["Course Name", "Teacher", "Time", "Students Enrolled", ""];
    const tabs = el("div", {},
        el("button", { className: "tab" + (tab === "mine" ? " active" : ""), onclick: () => reload("mine") }, "Your Courses"),
        el("button", { className: "tab" + (tab === "all" ? " active" : ""), onclick: () => reload("all") }, "Add Courses"));
    shell(user, tabs, tab === "mine" ? table(head, mine) : table(head, all));
}

// teacher view; only sees their course and roster
async function showTeacher(user) {
    const courses = (await api("GET", "/api/courses")).filter((c) => c.mine);
    shell(user, el("h2", {}, "Your Courses"), table(["Course Name", "Teacher", "Time", "Students Enrolled", ""],
        courses.map((c) => [c.name, c.teacher, c.time, c.enrolled + "/" + c.capacity,
            el("button", { onclick: () => attempt(() => showRoster(user, c.id)) }, "View roster")])));
}

// roster for one course
async function showRoster(user, courseId) {
    const data = await api("GET", "/api/courses/" + courseId + "/roster");
    const rows = data.students.map((s) => {
        const input = el("input", { type: "number", min: 0, max: 100, step: "any", value: s.grade === null ? "" : s.grade });
        const save = el("button", { onclick: () => attempt(async () => {
            await api("PUT", "/api/courses/" + courseId + "/grades/" + s.student_id, { grade: Number(input.value) });
            say("Saved grade for " + s.name, false);
        }) }, "Save");
        return [s.name, input, save];
    });
    shell(user, el("button", { onclick: () => showTeacher(user) }, "Back"), el("h2", {}, data.course.name),
        table(["Student Name", "Grade", ""], rows));
}

// admin view
function showAdmin(user) {
    shell(user, el("h2", {}, "Admin"),
        el("p", {}, "Create, read, update and delete all users, courses and enrollments/grades."),
        el("a", { href: "/admin/" }, "Open the admin database page"));
}

// runs the page; asks if student, teacher, or admin
async function start() {
    let user;
    try { user = await api("GET", "/api/me"); } catch (e) { return showLogin(); }
    try {
        if (user.role === "student") await showStudent(user, "mine");
        else if (user.role === "teacher") await showTeacher(user);
        else showAdmin(user);
    } catch (err) {
        say(err.message, true);
    }
}

start();