// Search backwards from the empty board, then return forward click indices.
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <unordered_set>
#include <vector>

namespace {
constexpr int MaxCards = 500;
constexpr int Words = (MaxCards + 63) / 64;
constexpr int MaxTypes = MaxCards / 3 + 1;
using Clock = std::chrono::steady_clock;

struct Bits {
    std::array<uint64_t, Words> words{};
    bool operator==(const Bits& other) const { return words == other.words; }
    void set(int i) { words[i / 64] |= uint64_t{1} << (i % 64); }
    void clear(int i) { words[i / 64] &= ~(uint64_t{1} << (i % 64)); }
    bool has(int i) const { return (words[i / 64] >> (i % 64)) & 1; }
    bool intersects(const Bits& other) const {
        for (int i = 0; i < Words; ++i) {
            if (words[i] & other.words[i]) return true;
        }
        return false;
    }
    int intersection_size(const Bits& other) const {
        int count = 0;
        for (int i = 0; i < Words; ++i) {
            count += __builtin_popcountll(words[i] & other.words[i]);
        }
        return count;
    }
    void merge(const Bits& other) {
        for (int i = 0; i < Words; ++i) words[i] |= other.words[i];
    }
};

struct Hash {
    size_t operator()(const Bits& bits) const {
        uint64_t hash = 0x9e3779b97f4a7c15ULL;
        for (auto word : bits.words) {
            word = (word ^ (word >> 30)) * 0xbf58476d1ce4e5b9ULL;
            word = (word ^ (word >> 27)) * 0x94d049bb133111ebULL;
            hash ^= word ^ (word >> 31);
            hash = (hash << 13) | (hash >> 51);
        }
        return hash;
    }
};

struct Card { int type, x, y, layer; };
struct Path { int previous, chosen; };
struct State {
    Bits remaining;
    std::array<unsigned char, MaxTypes> tray{};
    int occupied = 0;
    double score = 0;
    int path = -1;
    int parent = -1;
    int chosen = -1;
};

struct Search {
    std::vector<Card> cards;
    std::vector<Bits> dependencies, ancestors;
    std::vector<Path> paths;
    Clock::time_point deadline;
    int width, type_count = 0, best_depth = 0;
    uint64_t visited = 0;
    bool pruned = false;

    Search(std::vector<Card> input, int beam_width, double seconds)
        : cards(std::move(input)), dependencies(cards.size()), ancestors(cards.size()),
          deadline(Clock::now() + std::chrono::duration_cast<Clock::duration>(
              std::chrono::duration<double>(seconds))), width(beam_width) {
        std::vector<int> indices;
        for (int i = 0; i < int(cards.size()); ++i) {
            indices.push_back(i);
            type_count = std::max(type_count, cards[i].type + 1);
            for (int j = 0; j < int(cards.size()); ++j) {
                if (cards[j].layer < cards[i].layer
                    && std::abs(int64_t(cards[j].x) - cards[i].x) < 8
                    && std::abs(int64_t(cards[j].y) - cards[i].y) < 8) {
                    dependencies[i].set(j);
                }
            }
        }
        std::sort(indices.begin(), indices.end(), [&](int a, int b) {
            return cards[a].layer < cards[b].layer;
        });
        for (int i : indices) {
            ancestors[i] = dependencies[i];
            for (int j = 0; j < int(cards.size()); ++j) {
                if (dependencies[i].has(j)) ancestors[i].merge(ancestors[j]);
            }
        }
    }

    void evaluate(State& state) const {
        double obstruction = 0, held_cost = 0, ready = 0;
        std::array<std::array<int, 3>, MaxTypes> costs;
        for (auto& cost : costs) cost = {MaxCards + 1, MaxCards + 1, MaxCards + 1};
        for (int i = 0; i < int(cards.size()); ++i) {
            if (!state.remaining.has(i)) continue;
            int cost = ancestors[i].intersection_size(state.remaining);
            obstruction += std::log1p(cost);
            auto& best = costs[cards[i].type];
            if (cost < best[2]) {
                best[2] = cost;
                if (best[2] < best[1]) std::swap(best[2], best[1]);
                if (best[1] < best[0]) std::swap(best[1], best[0]);
            }
        }
        for (int type = 0; type < type_count; ++type) {
            for (int j = 0; j < state.tray[type]; ++j) held_cost += costs[type][j];
            if (costs[type][std::max(0, int(state.tray[type]) - 1)] == 0) ++ready;
        }
        state.score = -obstruction - 2 * held_cost - 4 * state.occupied + 0.5 * ready;
    }

    // In reverse, undoing a triple adds two tray cards; other moves remove one.
    void expand(const State& state, std::vector<State>& next,
                std::unordered_set<Bits, Hash>& seen) {
        for (int i = 0; i < int(cards.size()); ++i) {
            if (!state.remaining.has(i) || dependencies[i].intersects(state.remaining)) continue;
            int type = cards[i].type;
            int delta = state.tray[type] ? -1 : 2;
            if (state.occupied + delta > 6) continue;
            State child = state;
            child.remaining.clear(i);
            if (!seen.insert(child.remaining).second) continue;
            child.occupied += delta;
            child.tray[type] = (child.tray[type] + 2) % 3;
            child.parent = state.path;
            child.chosen = i;
            evaluate(child);
            next.push_back(child);
            ++visited;
        }
    }

    const char* solve(std::vector<int>& order) {
        State start;
        for (int i = 0; i < int(cards.size()); ++i) start.remaining.set(i);
        std::vector<State> beam{start};
        for (int depth = 0; depth < int(cards.size()); ++depth) {
            std::vector<State> next;
            std::unordered_set<Bits, Hash> seen;
            seen.reserve(beam.size() * 10);
            for (const auto& state : beam) {
                if (Clock::now() >= deadline) return "timeout";
                expand(state, next, seen);
            }
            if (next.empty()) return pruned ? "search_exhausted" : "unsatisfiable";
            if (int(next.size()) > width) {
                pruned = true;
                std::nth_element(next.begin(), next.begin() + width, next.end(),
                    [](const State& a, const State& b) { return a.score > b.score; });
                next.resize(width);
            }
            for (auto& state : next) {
                state.path = int(paths.size());
                paths.push_back({state.parent, state.chosen});
            }
            beam = std::move(next);
            best_depth = depth + 1;
        }
        for (int p = beam[0].path; p >= 0; p = paths[p].previous) {
            order.push_back(paths[p].chosen);
        }
        return "solved";
    }
};
}  // namespace

int main() {
    int count, width;
    double seconds;
    if (!(std::cin >> count >> width >> seconds) || count < 1 || count > MaxCards
        || width < 1 || width > 100000 || !std::isfinite(seconds) || seconds <= 0) return 2;
    std::vector<Card> cards(count);
    std::array<int, MaxTypes> counts{};
    for (auto& card : cards) {
        if (!(std::cin >> card.type >> card.x >> card.y >> card.layer)
            || card.type < 0 || card.type >= MaxTypes) return 2;
        ++counts[card.type];
    }
    for (int amount : counts) if (amount % 3) return 2;
    Search search(std::move(cards), width, seconds);
    std::vector<int> order;
    const char* status = search.solve(order);
    std::cout << "{\"status\":\"" << status << "\",\"visited\":" << search.visited
              << ",\"bestDepth\":" << search.best_depth << ",\"order\":[";
    for (size_t i = 0; i < order.size(); ++i) {
        if (i) std::cout << ',';
        std::cout << order[i];
    }
    std::cout << "]}\n";
}
