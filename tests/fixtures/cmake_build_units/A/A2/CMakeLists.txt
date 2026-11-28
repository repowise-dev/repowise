add_library(A2 STATIC
    src/a2.cpp
    src/a2_parser.cpp
    src/a2_model.cpp
    src/a2_store.cpp
    src/a2_view.cpp
)
target_include_directories(A2 PUBLIC include)
target_link_libraries(A2 PUBLIC ACommon B2)
