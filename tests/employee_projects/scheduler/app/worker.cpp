#include "api.hpp"
int main() {
    return scheduler_ready() ? 0 : 1;
}
