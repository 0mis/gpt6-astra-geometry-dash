#include <Geode/Geode.hpp>
#include <Geode/modify/CCScheduler.hpp>
#include <Geode/modify/PlayLayer.hpp>
#include <Geode/modify/CCEGLView.hpp>
#include <nlohmann/json.hpp>
#include <Windows.h>
#include <fstream>
#include <mutex>
#include <unordered_map>

using namespace geode::prelude;
using json = nlohmann::json;

namespace {
std::filesystem::path root;
std::string lastRequest;
std::unordered_map<std::string, std::pair<std::string, json>> answers;
uint64_t ticks = 0;
uint64_t ticksLeft = 0;
uint64_t menuHandoffTicks = 0;
uint64_t leaseUntil = 0;
int frameTicks = 4;
bool held1 = false;
bool held2 = false;
bool want1 = false;
bool want2 = false;
std::string activeRequest;
std::string activeSignature;
bool armed = true;
uint64_t generation = 1;
PlayLayer* previousLevel = nullptr;
std::string finishAfterCapture;
json lastDeath = nullptr;

// The game publishes only its own OpenGL back buffer. The recorder acknowledges
// a frame only after FFmpeg reports encoding it; no desktop or audio is involved.
struct alignas(8) FrameHeader {
    char magic[8];
    volatile LONG64 sequence;
    uint64_t tick, generation, uptime;
    uint32_t width, height, bytes, valid;
    volatile LONG64 ackSequence;
    uint64_t ackTick, ackGeneration, ackUptime;
    uint32_t recorderPID, reserved;
    uint64_t processEpoch;
    char padding[24];
};
static_assert(sizeof(FrameHeader) == 128);
HANDLE frameMapping = nullptr;
FrameHeader* frames = nullptr;
constexpr size_t mappingBytes = 32 * 1024 * 1024;

bool recordingAck() {
    if (!frames) return false;
    auto seq = InterlockedCompareExchange64(&frames->ackSequence, 0, 0);
    if (seq <= 0) return false;
    auto now = GetTickCount64();
    bool result = frames->ackTick == ticks && frames->ackGeneration == generation &&
        frames->ackUptime <= now && now - frames->ackUptime < 1500 && frames->recorderPID != 0;
    MemoryBarrier();
    return result && seq == InterlockedCompareExchange64(&frames->ackSequence, 0, 0);
}

void captureRenderedFrame() {
    if (!frames) return;
    auto now = GetTickCount64();
    // A paused physics tick renders the same world. Keep it available long
    // enough to copy consistently instead of overwriting a 6 MB buffer at 120 Hz.
    if (frames->valid && frames->tick == ticks && frames->generation == generation &&
        now - frames->uptime < (PlayLayer::get() ? 250 : 33)) return;
    GLint viewport[4], pack, rowLength, skipRows, skipPixels, readBuffer, packBuffer, readFBO;
    glGetIntegerv(GL_VIEWPORT, viewport);
    auto width = viewport[2], height = viewport[3];
    auto bytes = static_cast<size_t>(width) * height * 3;
    if (width <= 0 || height <= 0 || bytes > mappingBytes - sizeof(FrameHeader)) return;
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &rowLength);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &skipRows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &skipPixels);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &readFBO);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &packBuffer);
    glGetIntegerv(GL_READ_BUFFER, &readBuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glReadBuffer(GL_BACK);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    InterlockedIncrement64(&frames->sequence);
    frames->valid = 0;
    glReadPixels(viewport[0], viewport[1], width, height, GL_BGR, GL_UNSIGNED_BYTE,
        reinterpret_cast<char*>(frames) + sizeof(FrameHeader));
    frames->width = width;
    frames->height = height;
    frames->bytes = static_cast<uint32_t>(bytes);
    frames->tick = ticks;
    frames->generation = generation;
    frames->uptime = GetTickCount64();
    frames->valid = 1;
    InterlockedIncrement64(&frames->sequence);
    glPixelStorei(GL_PACK_ALIGNMENT, pack);
    glPixelStorei(GL_PACK_ROW_LENGTH, rowLength);
    glPixelStorei(GL_PACK_SKIP_ROWS, skipRows);
    glPixelStorei(GL_PACK_SKIP_PIXELS, skipPixels);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, packBuffer);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, readFBO);
    glReadBuffer(readBuffer);
}

void writeFile(std::string const& name, json const& value) {
    auto destination = root / name;
    auto temp = root / (name + ".tmp");
    { std::ofstream out(temp, std::ios::binary); out << value.dump(2); }
    MoveFileExW(temp.c_str(), destination.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

void writeResponse(json const& value) {
    // Each response is immutable and has its own path. A polling reader never
    // prevents publication by holding the previous response file open.
    auto name = std::string("responses/") + value.at("request_id").get<std::string>() + ".json";
    if (!std::filesystem::exists(root / name)) writeFile(name, value);
}

json rectangle(cocos2d::CCRect const& rect) {
    return {rect.origin.x, rect.origin.y, rect.size.width, rect.size.height};
}

json objectState(GameObject* obj) {
    if (!obj) return nullptr;
    auto p = obj->getPosition();
    return {{"id", obj->m_objectID}, {"type", static_cast<int>(obj->m_objectType)},
        {"x", p.x}, {"y", p.y}, {"rotation", obj->getRotation()},
        {"disabled", obj->m_isDisabled}, {"activated", obj->m_isActivated},
        {"rect", rectangle(obj->getObjectRect())}, {"radius", obj->getObjectRadius()},
        {"raw_radius", obj->m_objectRadius},
        {"passable", obj->m_isPassable}};
}

json playerState(PlayerObject* player) {
    if (!player) return nullptr;
    auto p = player->getPosition();
    return {{"x", p.x}, {"y", p.y}, {"vy", player->m_yVelocity},
        {"dead", player->m_isDead}, {"on_ground", player->m_isOnGround},
        {"ship", player->m_isShip}, {"ball", player->m_isBall}, {"ufo", player->m_isBird},
        {"wave", player->m_isDart}, {"robot", player->m_isRobot}, {"spider", player->m_isSpider},
        {"swing", player->m_isSwing}, {"upside_down", player->m_isUpsideDown},
        {"size", player->m_vehicleSize}, {"speed", player->m_playerSpeed},
        {"rect", rectangle(player->getObjectRect())}, {"rotation", player->getRotation()}};
}

json state() {
    auto pl = PlayLayer::get();
    json data = {{"pid", GetCurrentProcessId()}, {"uptime_ms", GetTickCount64()},
        {"bridge_version", "0.7.1"}, {"gd_version", GEODE_GD_VERSION_STRING},
        {"in_level", pl != nullptr}, {"armed", armed}, {"ticks", ticks},
        {"ticks_left", ticksLeft}, {"generation", generation}, {"recorded_current_frame", recordingAck()},
        {"frame_sequence", frames ? frames->sequence : 0}, {"original_collision_rules", true}};
    if (pl) {
        data["p1"] = playerState(pl->m_player1);
        data["p2"] = playerState(pl->m_player2);
        data["percent"] = pl->getCurrentPercent();
        data["completed"] = pl->m_hasCompletedLevel;
        data["end_animation_started"] = pl->m_levelEndAnimationStarted;
        data["practice"] = pl->m_isPracticeMode;
        data["paused_menu"] = pl->m_isPaused;
        data["level_time"] = pl->m_gameState.m_levelTime;
        data["dual"] = pl->m_gameState.m_isDualMode;
        data["started"] = pl->m_started;
        data["ignore_damage"] = pl->m_isIgnoreDamageEnabled;
        data["test_mode"] = pl->m_isTestMode;
        data["last_death"] = lastDeath;
        if (pl->m_level) {
            data["level_name"] = std::string(pl->m_level->m_levelName);
            data["level_id"] = static_cast<int>(pl->m_level->m_levelID);
            data["saved_normal_percent"] = static_cast<int>(pl->m_level->m_normalPercent);
        }
    }
    return data;
}

json nearbyObjects() {
    auto rows = json::array();
    auto pl = PlayLayer::get();
    if (!pl || !pl->m_player1 || !pl->m_objects) return rows;
    float px = pl->m_player1->getPositionX();
    for (auto obj : CCArrayExt<GameObject*>(pl->m_objects)) {
        auto p = obj->getPosition();
        if (p.x < px - 120 || p.x > px + 900) continue;
        rows.push_back(objectState(obj));
        if (rows.size() >= 1500) break;
    }
    return rows;
}

void buttons(CCNode* node, bool visible, json& rows) {
    if (!node || rows.size() > 300) return;
    visible = visible && node->isVisible();
    if (!visible) return;
    if (auto item = typeinfo_cast<CCMenuItem*>(node); item && item->isEnabled()) {
        auto id = reinterpret_cast<uintptr_t>(item);
        auto point = item->convertToWorldSpace({item->getContentSize().width / 2, item->getContentSize().height / 2});
        rows.push_back({{"id", id}, {"x", point.x}, {"y", point.y},
            {"width", item->getContentSize().width}, {"height", item->getContentSize().height},
            {"node_id", item->getID()}});
    }
    if (auto children = node->getChildren()) {
        for (auto child : CCArrayExt<CCNode*>(children)) { buttons(child, visible, rows); }
    }
}

void labels(CCNode* node, bool visible, json& rows) {
    if (!node || rows.size() > 150) return;
    visible = visible && node->isVisible();
    if (!visible) return;
    std::string text;
    if (auto label = typeinfo_cast<CCLabelBMFont*>(node)) text = label->getString();
    else if (auto label = typeinfo_cast<CCLabelTTF*>(node)) text = label->getString();
    if (!text.empty()) {
        auto p = node->convertToWorldSpace({0, 0});
        rows.push_back({{"text", text}, {"x", p.x}, {"y", p.y}});
    }
    if (auto children = node->getChildren())
        for (auto child : CCArrayExt<CCNode*>(children)) labels(child, visible, rows);
}

void releaseInputs(PlayLayer* pl) {
    if (pl) {
        if (held1) pl->handleButton(false, 1, true);
        if (held2) pl->handleButton(false, 1, false);
    }
    held1 = held2 = false;
}

void finish(std::string const& reason) {
    if (activeRequest.empty()) return;
    json result = {{"request_id", activeRequest}, {"ok", true}, {"reason", reason}, {"state", state()}};
    answers[activeRequest] = {activeSignature, result};
    writeResponse(result);
    activeRequest.clear();
}

void poll() {
    std::filesystem::path file;
    for (auto const& entry : std::filesystem::directory_iterator(root / "requests")) {
        if (entry.is_regular_file() && entry.path().extension() == ".json") { file = entry.path(); break; }
    }
    if (file.empty()) return;
    json request;
    try { std::ifstream stream(file); request = json::parse(stream); }
    catch (...) { return; }
    auto id = request.value("request_id", "");
    if (id.size() != 32 || id.find_first_not_of("0123456789abcdef") != std::string::npos) return;
    auto signature = request.dump();
    lastRequest = id;
    // Archive only after the input stream has closed. The client never opens an
    // inbox file after atomically publishing it.
    MoveFileExW(file.c_str(), (root / "processed" / file.filename()).c_str(), MOVEFILE_REPLACE_EXISTING);
    json response = {{"request_id", id}, {"ok", false}};
    try {
        if (request.value("game_pid", 0u) != GetCurrentProcessId() || !frames ||
            request.value("game_epoch_ms", uint64_t(0)) != frames->processEpoch)
            throw std::runtime_error("Request belongs to a different game process; no action performed");
        if (auto it = answers.find(id); it != answers.end()) {
            if (it->second.first != signature) throw std::runtime_error("request_id argument mismatch");
            writeResponse(it->second.second);
            return;
        }
        auto op = request.at("op").get<std::string>();
        if (op == "status" || op == "observe") {
            response["state"] = state();
            if (op == "observe") response["nearby_objects"] = nearbyObjects();
            json items = json::array();
            buttons(CCDirector::sharedDirector()->getRunningScene(), true, items);
            response["buttons"] = items;
            json text = json::array();
            labels(CCDirector::sharedDirector()->getRunningScene(), true, text);
            response["labels"] = text;
        } else {
            auto now = GetTickCount64();
            auto lease = request.at("recording_lease_until_ms").get<uint64_t>();
            if (!request.value("recording_healthy", false) || lease <= now || lease > now + 30000)
                throw std::runtime_error("A current verified recording lease is required");
            if (!activeRequest.empty()) throw std::runtime_error("A step request is already in flight");
            auto pl = PlayLayer::get();
            if (op == "windowed") {
                if (pl) throw std::runtime_error("Display configuration is menu-only");
                PlatformToolbox::toggleFullScreen(false, false, true);
            } else if (op == "activate") {
                if (pl && !pl->m_isPaused && !pl->m_hasCompletedLevel)
                    throw std::runtime_error("Pause the level before activating menu buttons");
                json items = json::array();
                buttons(CCDirector::sharedDirector()->getRunningScene(), true, items);
                auto wanted = request.at("button_id").get<uintptr_t>();
                bool found = false;
                for (auto const& item : items) if (item.at("id").get<uintptr_t>() == wanted) found = true;
                if (!found) throw std::runtime_error("Button no longer present");
                if (pl && pl->m_hasCompletedLevel) {
                    releaseInputs(pl);
                    menuHandoffTicks = 480;
                    leaseUntil = lease;
                }
                // Activate after traversal, because a button may delete its popup.
                reinterpret_cast<CCMenuItem*>(wanted)->activate();
            } else if (op == "pause") {
                if (!pl) throw std::runtime_error("No active level");
                releaseInputs(pl);
                pl->pauseGame(false);
            } else if (op == "step") {
                if (!pl) throw std::runtime_error("No active level");
                if (pl->m_isPaused) throw std::runtime_error("Game pause menu must be closed");
                if (!recordingAck()) throw std::runtime_error("The current native game frame is not acknowledged by the encoder");
                auto count = request.at("ticks").get<int>();
                if (count < 1 || count > 480) throw std::runtime_error("Tick count outside 1..480");
                frameTicks = request.value("frame_ticks", 4);
                if (frameTicks < 1 || frameTicks > 4) throw std::runtime_error("Recorded frame may cover only 1..4 physics ticks");
                ticksLeft = count;
                leaseUntil = lease;
                want1 = request.value("hold", false);
                want2 = request.value("hold2", false);
                activeRequest = id;
                activeSignature = signature;
                finishAfterCapture.clear();
                return;
            } else if (op == "restart") {
                if (!pl) throw std::runtime_error("No active level");
                releaseInputs(pl);
                pl->resetLevel();
                ticksLeft = 0;
                ticks = 0;
                ++generation;
                lastDeath = nullptr;
            } else throw std::runtime_error("Unsupported operation");
            response["state"] = state();
        }
        response["ok"] = true;
    } catch (std::exception const& error) { response["error"] = error.what(); }
    answers[id] = {signature, response};
    writeResponse(response);
}
}

class $modify(AstraScheduler, CCScheduler) {
    void update(float dt) {
        if (root.empty()) { CCScheduler::update(dt); return; }
        poll();
        auto pl = PlayLayer::get();
        if (pl != previousLevel) {
            previousLevel = pl;
            ticks = 0;
            ++generation;
            lastDeath = nullptr;
            menuHandoffTicks = 0;
            held1 = held2 = want1 = want2 = false;
            if (!activeRequest.empty()) {
                ticksLeft = 0;
                finishAfterCapture = "scene_changed_recorded_frozen";
            }
        }
        if (!finishAfterCapture.empty() && recordingAck()) {
            finish(finishAfterCapture);
            finishAfterCapture.clear();
        }
        if (!pl || !armed || pl->m_isPaused) { CCScheduler::update(dt); return; }
        if (menuHandoffTicks) {
            // The ordinary results buttons schedule an animated scene change.
            // Advance only the completed scene, with recorded frames. A new or
            // restarted attempt stays frozen and cannot inherit these ticks.
            if (!pl->m_hasCompletedLevel) {
                menuHandoffTicks = 0;
                ticks = 0;
                ++generation;
                return;
            }
            if (GetTickCount64() >= leaseUntil) { menuHandoffTicks = 0; return; }
            if (!recordingAck()) return;
            for (int i = 0; i < 4 && menuHandoffTicks; ++i) {
                CCScheduler::update(1.f / 240.f);
                ++ticks;
                --menuHandoffTicks;
                if (PlayLayer::get() != pl) { menuHandoffTicks = 0; break; }
            }
            return;
        }
        if (ticksLeft == 0) return;
        if (GetTickCount64() >= leaseUntil) {
            ticksLeft = 0;
            releaseInputs(pl);
            finish("recording_lease_expired_frozen");
            return;
        }
        if (!recordingAck()) return;
        if (held1 != want1) { pl->handleButton(want1, 1, true); held1 = want1; }
        if (held2 != want2) { pl->handleButton(want2, 1, false); held2 = want2; }
        // At most 1/60 second of the original 240 Hz physics per recorded frame.
        // Individual one-tick requests still retain 1/240-second input precision.
        for (int i = 0; i < frameTicks && ticksLeft; ++i) {
            CCScheduler::update(1.f / 240.f);
            ++ticks;
            --ticksLeft;
            if (PlayLayer::get() != pl) {
                ticksLeft = 0;
                finishAfterCapture = "scene_changed_recorded_frozen";
                break;
            }
            if (pl->m_player1 && pl->m_player1->m_isDead) {
                ticksLeft = 0;
                releaseInputs(pl);
                finishAfterCapture = "died_recorded";
            } else if (ticksLeft == 0) finishAfterCapture = "requested_ticks_finished_recorded_frozen";
        }
    }
};

class $modify(AstraDeathObservation, PlayLayer) {
    void destroyPlayer(PlayerObject* player, GameObject* object) {
        bool wasDead = !player || player->m_isDead;
        json observation = nullptr;
        // Observation is best-effort and never replaces the original collision.
        try {
            if (!wasDead) observation = {{"generation", generation}, {"tick", ticks + 1},
                {"player_before", playerState(player)}, {"object", objectState(object)}};
        } catch (...) {}
        PlayLayer::destroyPlayer(player, object);
        if (!wasDead && player->m_isDead && !observation.is_null())
            lastDeath = std::move(observation);
    }
};

class $modify(AstraFrameCapture, CCEGLView) {
    void swapBuffers() {
        captureRenderedFrame();
        CCEGLView::swapBuffers();
    }
};

$on_mod(Loaded) {
    root = Mod::get()->getSaveDir() / "recorded-bridge";
    std::filesystem::create_directories(root);
    for (auto const& folder : {"requests", "responses", "processed"})
        std::filesystem::create_directories(root / folder);
    auto mappingName = L"Local\\AstraGDFrame_" + std::to_wstring(GetCurrentProcessId());
    frameMapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(mappingBytes), mappingName.c_str());
    if (frameMapping) frames = static_cast<FrameHeader*>(MapViewOfFile(frameMapping, FILE_MAP_ALL_ACCESS, 0, 0, mappingBytes));
    if (frames) {
        memset(frames, 0, sizeof(FrameHeader));
        memcpy(frames->magic, "ASTRAGD1", 8);
        frames->processEpoch = GetTickCount64();
    }
    writeFile("ready.json", {{"pid", GetCurrentProcessId()}, {"protocol", 2},
        {"version", "0.7.1"}, {"frame_mapping", frames != nullptr},
        {"process_epoch_ms", frames ? frames->processEpoch : 0},
        {"ready_at_uptime_ms", GetTickCount64()}, {"initially_frozen_in_levels", true}});
}
