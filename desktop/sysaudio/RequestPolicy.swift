import Foundation

enum AudioRequestPolicy {
    static func allows(method: String, path: String, headers: [String]) -> Bool {
        var values: [String: String] = [:]
        for line in headers {
            guard let colon = line.firstIndex(of: ":") else { return false }
            let name = line[..<colon].lowercased()
            guard name == name.trimmingCharacters(in: .whitespaces), values[name] == nil else { return false }
            values[name] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        guard let host = values["host"]?.lowercased(),
              host.range(of: #"^(127\.0\.0\.1|localhost)(:[0-9]{1,5})?$"#, options: .regularExpression) != nil,
              values["origin"] == nil, values["sec-fetch-site"] == nil else { return false }
        if path == "/health" { return method == "GET" }
        guard values["x-sinkduce-audio-proxy"] == "1" else { return false }
        switch path {
        case "/permissions", "/pcm": return method == "GET"
        case "/warmup", "/stop": return method == "POST"
        default: return false
        }
    }
}
