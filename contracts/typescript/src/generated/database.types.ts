export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  public: {
    Tables: {
      aggregator_logs: {
        Row: {
          action: string
          confidence: number
          created_at: string
          signal_id: string
          signal_source: string
          strategy_signal_id_a: string | null
          strategy_signal_id_b: string | null
          symbol: string
        }
        Insert: {
          action: string
          confidence: number
          created_at?: string
          signal_id: string
          signal_source: string
          strategy_signal_id_a?: string | null
          strategy_signal_id_b?: string | null
          symbol: string
        }
        Update: {
          action?: string
          confidence?: number
          created_at?: string
          signal_id?: string
          signal_source?: string
          strategy_signal_id_a?: string | null
          strategy_signal_id_b?: string | null
          symbol?: string
        }
        Relationships: [
          {
            foreignKeyName: "aggregator_logs_strategy_signal_id_a_fkey"
            columns: ["strategy_signal_id_a"]
            isOneToOne: false
            referencedRelation: "strategy_logs"
            referencedColumns: ["signal_id"]
          },
          {
            foreignKeyName: "aggregator_logs_strategy_signal_id_b_fkey"
            columns: ["strategy_signal_id_b"]
            isOneToOne: false
            referencedRelation: "strategy_logs"
            referencedColumns: ["signal_id"]
          },
        ]
      }
      daily_ohlcv: {
        Row: {
          close: number
          date: string
          high: number
          low: number
          open: number
          symbol: string
          turnover: number
          volume: number
        }
        Insert: {
          close: number
          date: string
          high: number
          low: number
          open: number
          symbol: string
          turnover: number
          volume: number
        }
        Update: {
          close?: number
          date?: string
          high?: number
          low?: number
          open?: number
          symbol?: string
          turnover?: number
          volume?: number
        }
        Relationships: []
      }
      dashboard_admins: {
        Row: {
          created_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          user_id?: string
        }
        Relationships: []
      }
      event_paper_stage_dispatches: {
        Row: {
          attempt_id: string | null
          attempted_at: string | null
          confirmed_at: string | null
          created_at: string
          destination_topic: string
          input_payload: Json
          input_payload_sha256: string
          input_signal_id: string
          last_error: string | null
          output_payload: Json
          output_payload_sha256: string
          pubsub_message_id: string | null
          stage: string
          status: string
          updated_at: string
        }
        Insert: {
          attempt_id?: string | null
          attempted_at?: string | null
          confirmed_at?: string | null
          created_at?: string
          destination_topic: string
          input_payload: Json
          input_payload_sha256: string
          input_signal_id: string
          last_error?: string | null
          output_payload: Json
          output_payload_sha256: string
          pubsub_message_id?: string | null
          stage: string
          status?: string
          updated_at?: string
        }
        Update: {
          attempt_id?: string | null
          attempted_at?: string | null
          confirmed_at?: string | null
          created_at?: string
          destination_topic?: string
          input_payload?: Json
          input_payload_sha256?: string
          input_signal_id?: string
          last_error?: string | null
          output_payload?: Json
          output_payload_sha256?: string
          pubsub_message_id?: string | null
          stage?: string
          status?: string
          updated_at?: string
        }
        Relationships: []
      }
      gateway_risk_reservations: {
        Row: {
          notional_amount: number
          order_id: string
          reason: string | null
          released_at: string | null
          reserved_at: string
          risk_amount: number
          side: string
          status: string
          symbol: string
          trade_mode: string
          trading_date: string
        }
        Insert: {
          notional_amount: number
          order_id: string
          reason?: string | null
          released_at?: string | null
          reserved_at?: string
          risk_amount: number
          side: string
          status?: string
          symbol: string
          trade_mode: string
          trading_date: string
        }
        Update: {
          notional_amount?: number
          order_id?: string
          reason?: string | null
          released_at?: string | null
          reserved_at?: string
          risk_amount?: number
          side?: string
          status?: string
          symbol?: string
          trade_mode?: string
          trading_date?: string
        }
        Relationships: []
      }
      market_regime: {
        Row: {
          buy_enabled: boolean
          confidence: number
          created_at: string
          metrics: Json
          position_size_multiplier: number
          rationale: Json
          regime: string
          source: string
          valid_date: string
        }
        Insert: {
          buy_enabled: boolean
          confidence: number
          created_at?: string
          metrics?: Json
          position_size_multiplier: number
          rationale?: Json
          regime: string
          source?: string
          valid_date: string
        }
        Update: {
          buy_enabled?: boolean
          confidence?: number
          created_at?: string
          metrics?: Json
          position_size_multiplier?: number
          rationale?: Json
          regime?: string
          source?: string
          valid_date?: string
        }
        Relationships: []
      }
      master_stocks: {
        Row: {
          is_active: boolean
          market_segment: string
          sector: string | null
          symbol: string
          symbol_name: string
          updated_at: string
        }
        Insert: {
          is_active?: boolean
          market_segment: string
          sector?: string | null
          symbol: string
          symbol_name: string
          updated_at?: string
        }
        Update: {
          is_active?: boolean
          market_segment?: string
          sector?: string | null
          symbol?: string
          symbol_name?: string
          updated_at?: string
        }
        Relationships: []
      }
      positions: {
        Row: {
          current_price: number
          entry_price: number
          holding_type: string
          max_hold_days: number | null
          opened_at: string
          position_generation_id: string | null
          quantity: number
          scheduled_exit_date: string | null
          scheduled_exit_time: string | null
          side: string
          stop_loss_price: number | null
          symbol: string
          target_price: number | null
          trade_type: string
          trailing_stop_pct: number | null
          unrealized_pnl: number
        }
        Insert: {
          current_price: number
          entry_price: number
          holding_type: string
          max_hold_days?: number | null
          opened_at?: string
          position_generation_id?: string | null
          quantity: number
          scheduled_exit_date?: string | null
          scheduled_exit_time?: string | null
          side: string
          stop_loss_price?: number | null
          symbol: string
          target_price?: number | null
          trade_type: string
          trailing_stop_pct?: number | null
          unrealized_pnl?: number
        }
        Update: {
          current_price?: number
          entry_price?: number
          holding_type?: string
          max_hold_days?: number | null
          opened_at?: string
          position_generation_id?: string | null
          quantity?: number
          scheduled_exit_date?: string | null
          scheduled_exit_time?: string | null
          side?: string
          stop_loss_price?: number | null
          symbol?: string
          target_price?: number | null
          trade_type?: string
          trailing_stop_pct?: number | null
          unrealized_pnl?: number
        }
        Relationships: []
      }
      strategy_logs: {
        Row: {
          action: string
          confidence: number
          created_at: string
          reasoning: string | null
          signal_id: string
          source: string
          symbol: string
        }
        Insert: {
          action: string
          confidence: number
          created_at?: string
          reasoning?: string | null
          signal_id: string
          source: string
          symbol: string
        }
        Update: {
          action?: string
          confidence?: number
          created_at?: string
          reasoning?: string | null
          signal_id?: string
          source?: string
          symbol?: string
        }
        Relationships: []
      }
      system_status: {
        Row: {
          daily_loss_limit: number
          daily_pnl: number
          id: number
          is_trading_allowed: boolean
          monthly_loss_limit: number
          monthly_pnl: number
          trade_mode: string
          trading_style: string
          updated_at: string
          weekly_loss_limit: number
          weekly_pnl: number
        }
        Insert: {
          daily_loss_limit: number
          daily_pnl?: number
          id?: number
          is_trading_allowed?: boolean
          monthly_loss_limit: number
          monthly_pnl?: number
          trade_mode?: string
          trading_style?: string
          updated_at?: string
          weekly_loss_limit: number
          weekly_pnl?: number
        }
        Update: {
          daily_loss_limit?: number
          daily_pnl?: number
          id?: number
          is_trading_allowed?: boolean
          monthly_loss_limit?: number
          monthly_pnl?: number
          trade_mode?: string
          trading_style?: string
          updated_at?: string
          weekly_loss_limit?: number
          weekly_pnl?: number
        }
        Relationships: []
      }
      trades_live: {
        Row: {
          executed_at: string
          order_id: string | null
          price: number
          quantity: number
          side: string
          signal_source: string
          symbol: string
          trade_id: string
          unified_signal_id: string | null
        }
        Insert: {
          executed_at?: string
          order_id?: string | null
          price: number
          quantity: number
          side: string
          signal_source: string
          symbol: string
          trade_id: string
          unified_signal_id?: string | null
        }
        Update: {
          executed_at?: string
          order_id?: string | null
          price?: number
          quantity?: number
          side?: string
          signal_source?: string
          symbol?: string
          trade_id?: string
          unified_signal_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "trades_live_unified_signal_id_fkey"
            columns: ["unified_signal_id"]
            isOneToOne: false
            referencedRelation: "aggregator_logs"
            referencedColumns: ["signal_id"]
          },
        ]
      }
      trades_paper: {
        Row: {
          executed_at: string
          order_id: string | null
          position_generation_id: string | null
          price: number
          quantity: number
          side: string
          signal_source: string
          symbol: string
          trade_id: string
          unified_signal_id: string | null
        }
        Insert: {
          executed_at?: string
          order_id?: string | null
          position_generation_id?: string | null
          price: number
          quantity: number
          side: string
          signal_source: string
          symbol: string
          trade_id: string
          unified_signal_id?: string | null
        }
        Update: {
          executed_at?: string
          order_id?: string | null
          position_generation_id?: string | null
          price?: number
          quantity?: number
          side?: string
          signal_source?: string
          symbol?: string
          trade_id?: string
          unified_signal_id?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "trades_paper_unified_signal_id_fkey"
            columns: ["unified_signal_id"]
            isOneToOne: false
            referencedRelation: "aggregator_logs"
            referencedColumns: ["signal_id"]
          },
        ]
      }
      watchlist: {
        Row: {
          created_at: string
          score: number
          selected_reasons: Json
          symbol: string
          symbol_name: string
          valid_date: string
        }
        Insert: {
          created_at?: string
          score: number
          selected_reasons?: Json
          symbol: string
          symbol_name: string
          valid_date: string
        }
        Update: {
          created_at?: string
          score?: number
          selected_reasons?: Json
          symbol?: string
          symbol_name?: string
          valid_date?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      event_paper_cas_strategy_reasoning: {
        Args: {
          p_expected_reasoning: string
          p_signal_id: string
          p_updated_reasoning: string
        }
        Returns: {
          applied: boolean
          reasoning: string
        }[]
      }
      event_paper_stage_dispatch: {
        Args: {
          p_action: string
          p_attempt_id?: string
          p_destination_topic?: string
          p_error?: string
          p_input_payload?: Json
          p_input_payload_sha256?: string
          p_input_signal_id: string
          p_occurred_at?: string
          p_output_payload?: Json
          p_output_payload_sha256?: string
          p_pubsub_message_id?: string
          p_stage: string
        }
        Returns: {
          attempt_id: string
          attempted_at: string
          confirmed_at: string
          destination_topic: string
          input_payload: Json
          input_payload_sha256: string
          input_signal_id: string
          last_error: string
          outcome: string
          output_payload: Json
          output_payload_sha256: string
          pubsub_message_id: string
          stage: string
          status: string
        }[]
      }
      gateway_check_and_reserve_risk: {
        Args: {
          p_notional_amount: number
          p_order_id: string
          p_risk_amount: number
          p_side: string
          p_symbol: string
          p_trade_mode: string
          p_trading_date: string
        }
        Returns: {
          active_risk_after: number
          active_risk_before: number
          daily_loss_limit: number
          daily_pnl: number
          monthly_loss_limit: number
          monthly_pnl: number
          passed: boolean
          reason: string
          reserved: boolean
          weekly_loss_limit: number
          weekly_pnl: number
        }[]
      }
      gateway_check_kill_switch: {
        Args: never
        Returns: {
          daily_loss_limit: number
          daily_pnl: number
          disabled: boolean
          id: number
          is_trading_allowed: boolean
          monthly_loss_limit: number
          monthly_pnl: number
          passed: boolean
          reason: string
          trade_mode: string
          trading_style: string
          updated_at: string
          weekly_loss_limit: number
          weekly_pnl: number
        }[]
      }
      gateway_release_risk_reservation: {
        Args: { p_order_id: string; p_reason?: string }
        Returns: {
          order_id: string
          released: boolean
          status: string
        }[]
      }
      oms_paper_apply_fill:
        | {
            Args: {
              p_executed_at: string
              p_expected_position_opened_at: string
              p_fill_price: number
              p_filled_quantity: number
              p_new_holding_type: string
              p_new_max_hold_days: number
              p_new_scheduled_exit_date: string
              p_new_stop_loss_price: number
              p_new_target_price: number
              p_new_trailing_stop_pct: number
              p_order_id: string
              p_side: string
              p_signal_source: string
              p_symbol: string
              p_trade_id: string
              p_unified_signal_id: string
            }
            Returns: {
              committed_trade_id: string
              outcome: string
              position_action: string
              reason: string
              resulting_position: Json
            }[]
          }
        | {
            Args: {
              p_executed_at: string
              p_expected_position_opened_at: string
              p_fill_price: number
              p_filled_quantity: number
              p_new_holding_type: string
              p_new_max_hold_days: number
              p_new_scheduled_exit_date: string
              p_new_scheduled_exit_time: string
              p_new_stop_loss_price: number
              p_new_target_price: number
              p_new_trailing_stop_pct: number
              p_order_id: string
              p_side: string
              p_signal_source: string
              p_symbol: string
              p_trade_id: string
              p_unified_signal_id: string
            }
            Returns: {
              committed_trade_id: string
              outcome: string
              position_action: string
              reason: string
              resulting_position: Json
            }[]
          }
      oms_paper_update_stop_loss: {
        Args: {
          p_expected_position_opened_at: string
          p_stop_loss_price: number
          p_symbol: string
        }
        Returns: {
          outcome: string
          reason: string
          resulting_position: Json
        }[]
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const
